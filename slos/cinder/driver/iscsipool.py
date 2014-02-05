from cinder import context
from cinder import utils
from cinder import db
from SoftLayer.utils import query_filter, NestedDict
from cinder.openstack.common import log as logging
import cinder.exception as exception
from cinder.exception import VolumeBackendAPIException
from slos.cinder.driver import api
from cinder.openstack.common import lockutils
from cinder.volume import utils as volume_utils

LOG = logging.getLogger(__name__)


class IscsiPool(api.SLClient):

    def __init__(self, *args, **kwargs):
        super(IscsiPool, self).__init__(*args, **kwargs)

    def local_volume_references(self, cntx):
        all_vols = db.volume_get_all(
            cntx, marker=None, limit=None, sort_key='created_at', sort_dir='desc')
        return [vol['id'] for vol in all_vols]

    def find_free_volume(self, size):
        _filter = NestedDict({})
        if self.configuration.sl_vol_order_ceil:
            _filter['iscsiNetworkStorage'][
                'capacityGb'] = query_filter('>=%s' % size)
        else:
            _filter['iscsiNetworkStorage']['capacityGb'] = query_filter(size)
        sl_volumes = self.client['Account'].getIscsiNetworkStorage(
                mask='mask[id,capacityGb,username,password,billingItem[id]]',
                filter=_filter.to_dict())
        if len(sl_volumes) == 0:
            return (None, None)
        sl_volumes = sorted(sl_volumes, key=lambda x: int(x['capacityGb']))
        cntx = context.get_admin_context()
        local_volumes = self.local_volume_references(cntx)
        for sl_vol in sl_volumes:
            if self.is_in_use(sl_vol['id'], cntx, local_volumes):
                continue
            return self.create_updates(sl_vol['id'])
        LOG.warn(_("No free volume found of size %s" % size))
        return (None, None)

    def is_in_use(self, sl_vol_id, cntx=None, local_volumes=None):
        if not cntx or not local_volumes:
            cntx = context.get_admin_context()
            local_volumes = self.local_volume_references(cntx)
        sl_vol_id = str(sl_vol_id)
        for volume_id in local_volumes:
            admin_meta = db.volume_admin_metadata_get(cntx, volume_id)
            if sl_vol_id == admin_meta.get('id', ''):
                return True
        return False

    def _get_vol_metadata(self, _id):
        admin_context = context.get_admin_context()
        metadata = db.volume_metadata_get(admin_context, _id)
        return metadata

    @lockutils.synchronized('sl_create_vol', 'cinder-', False)
    def create_volume(self, volume):
        metadata = self._get_vol_metadata(volume['id'])
        LOG.debug(
            _("Create volume called with name: %s, size: %s, id: %s" %
              (volume['display_name'], volume['size'], volume['id'])))
        if 'softlayer_volume_id' in metadata:
            if self.is_in_use(metdata['softlayer_volume_id']):
                raise exception.InvalidVolume(reason="Volume requested is already is in user")
            admin_meta, model_update = self.use_existing(
                volume['display_name'], metadata['softlayer_volume_id'])
            self._update(volume['id'], admin_meta)
        admin_meta, model_update = self.find_free_volume(volume['size'])
        if admin_meta:
            self._update(volume['id'], admin_meta)
            model_update['admin_meta'] = admin_meta
            return model_update
        if not self.configuration.sl_pool_real_order:
            raise VolumeBackendAPIException(
                data="Storage pool has been fully utilized."
                " Configuration does not allow driver to order new storage.")
        # here we have to order a new volume.
        items = self.find_items(volume['size'])
        if len(items) == 0:
            LOG.error(_("No item found for size %s" % volume['size']))
            raise VolumeBackendAPIException(
                data="iSCSI storage of %sGB size is not supported by SoftLayer." %
                volume['size'])
        LOG.debug(_("%d items found for size %s" % (len(items), volume['size'])))
        return self.order_iscsi(items, volume)

    def delete(self, volume):
        vol = self.get_sl_volume(volume)
        if not vol:
            LOG.warn("Corosponding volume for %s did not found. Assumming already deleted." % volume['id'])
            return
        sl_id = vol['id']
        connection = self.connect(sl_id)
        attach_info = self.parent._attch(connection)
        size_in_mb = 1024 * int(vol['capacityGb'])

        if not self.configuration.sl_pool_volume_clear in ("zero", "shred", "none"):
            raise exception.InvalidConfigurationValue(
                option='volume_clear',
                value=self.configuration.sl_pool_volume_clear)

        try:
            if self.configuration.sl_pool_volume_clear == 'zero':
                LOG.info("zeroing out volume")
                volume_utils.copy_volume(
                    '/dev/zero', attach_info['device']['path'], size_in_mb)
            elif self.configuration.sl_pool_volume_clear == 'shred':
                LOG.info("Shredding volume")
                utils.execute('shred', '-n3', '-s%dMiB' %
                              size_in_mb, attach_info['device']['path'], run_as_root=True)
        except Exception as e:
            LOG.error(_("Error while swiping out data. %s" % e))
            raise VolumeBackendAPIException(
                data="Error while erasing data from the volume %s" % e.message)
        finally:
            self.parent._detach_volume(attach_info)
        self.setNotes(sl_id, status='free')
        self._delete_metadata(volume['id'])
