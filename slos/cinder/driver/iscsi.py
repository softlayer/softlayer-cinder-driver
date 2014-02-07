# vim: tabstop=4 shiftwidth=4 softtabstop=4

"""
Volume driver for SoftLayer storage systems.


"""

from oslo.config import cfg

from cinder import exception
from cinder import utils
from cinder.openstack.common import log as logging
from cinder.exception import VolumeBackendAPIException
from cinder.volume import driver
from cinder import db
from cinder import context
from slos.cinder.driver.api import SLClient
from slos.cinder.driver.iscsipool import IscsiPool
from cinder.volume import utils as volume_utils

LOG = logging.getLogger(__name__)

sl_opts = [
    cfg.BoolOpt('sl_pool_real_order',
                default=0,
                help='What to do when pool is fully being used. Should new Order be placed?'),
    cfg.StrOpt('sl_strategy',
               default='monthly',
               help='How should the Volumes be ordered in SoftLayer. Possible values: monthly or pool'),
    cfg.BoolOpt('sl_vol_order_ceil',
                default=False,
                help='Should the driver order the first smallest bigger volume if size is not supported'),
    cfg.BoolOpt('sl_order_snap_space',
                default=True,
                help='Whether snapshot space for the volume be automatically ordered'),
    cfg.StrOpt('sl_use_name',
               default='none',
               help="How should driver use SL volume's name. Possible values: none, metadata, display_name."
               " 'none'=ignore SL's display name, 'metadata'= update volume metadata with key 'SL_iSCSI_Name',"
               " or 'display_name' = update volume's display name with SL's display name."),
    cfg.StrOpt('sl_datacenter',
               default='Dallas 5',
               help='SoftLayer Datacenter'),
    cfg.StrOpt('sl_pool_volume_clear',
               default='zero',
               help='How to erase contents of the volume when deleted. Possible values: zero, shred or none'),
    cfg.StrOpt('sl_vol_active_retry',
               default=10,
               help='Retry count to check volume is active'),
    cfg.StrOpt('sl_username',
               default=None,
               help='SoftLayer username'),
    cfg.StrOpt('sl_api_key',
               default=None,
               help='api_key for the softlayer account',
               secret=True)]


class SoftLayerISCSIDriver(driver.ISCSIDriver):

    """SoftLayer iSCSI volume driver. Implements the driver API
    for SoftLayer iSCSI volume service. The volumes created using
    this driver, will be created in SoftLayer.
    """

    def __init__(self, *args, **kwargs):
        super(SoftLayerISCSIDriver, self).__init__(*args, **kwargs)
        self.configuration.append_config_values(sl_opts)

    def _get_metadata(self, _id):
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, _id)
        return metadata

    def _find_sl_snapshot_id(self, volume_id, snapshot_id):
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, volume_id)
        return metadata.get(snapshot_id, None)

    def do_setup(self, context):
        """Setup the SoftLayer Volume driver.

        Called one time by the manager after the driver is loaded.
        Create the softlayer client.
        """
        if not self.configuration.sl_strategy in ('pool', 'monthly'):
            raise exception.InvalidConfigurationValue(
                option='sl_strategy',
                value=self.configuration.sl_strategy)

        if self.configuration.sl_strategy == 'monthly':
            LOG.debug("Driver Configured to use Monthly Strategy")
            Cls = SLClient
        else:
            LOG.debug("Driver Configured to use Pool Strategy")
            Cls = IscsiPool
        self.client = Cls(configuration=self.configuration, parent=self)

    def check_for_setup_error(self):
        """Check that the driver is working and can communicate.

        Invoke a web services API to make sure we can talk to the server.
        Also perform the datacenter lookup.
        """
        if not self.client:
            raise VolumeBackendAPIException(
                data="Unable to create SoftLayer Service client with the given configuration")
        try:
            LOG.debug("Checking if sed is accessible as root")
            utils.execute('sed', '-e',
                          "/discovery.sendtargets.auth.username/d", '-e',
                          "/discovery.sendtargets.auth.password/d",
                          '/etc/iscsi/iscsid.conf',
                          run_as_root=True)
        except Exception as e:
            LOG.error(_("Uable to execute 'sed' command on /etc/iscsi/iscsid.conf"
                        " make sure you have 'sed' entry in cinder's rootwrap.conf "
                        "or cinder or /etc/iscsi/iscsid.conf exists"))
            raise e
        return self.client.check_dc()

    def create_volume(self, volume):
        """Driver entry point for creating a new volume.

        Creates a new iSCSI volume, waits for it to become available.
        Once available, update the VolumeAdminMetadata with SoftLayer
        iSCSI storage ID. If volume is create with metadada
        `softlayer_volume_id=<ID>` then, driver just links the existing
        iSCSI storage with this volume.
        """
        model_update = {}
        model_update = self.client.create_volume(volume)
        model_update = self._check_display_name(model_update, volume['id'])
        return model_update

    def _check_display_name(self, model_update, vol_id, is_snapshot=False):
        if not 'display_name' in model_update:
            LOG.debug('display_name is not updated by create')
            return model_update
        if not self.configuration.sl_use_name in ('metadata',
                                                  'none',
                                                  'display_name'):
            LOG.warn(_("Configuration variable 'sl_use_name'"
                       " must have one of the three values: 'metadata', 'none', 'display_name'."
                       " Has %s. Assuming none instead." % self.configuration.sl_use_name))
            self.configuration.sl_use_name = 'none'
        if is_snapshot:
            update_meta = db.snapshot_metadata_update
        else:
            update_meta = db.volume_metadata_update
        if self.configuration.sl_use_name == 'metadata':
            admin_context = context.get_admin_context()
            update_meta(admin_context,
                        vol_id,
                        {'SL_iSCSI_Name': model_update['display_name']},
                        False)
            del model_update['display_name']
        elif self.configuration.sl_use_name == 'none':
            del model_update['display_name']
        return model_update

    def delete_volume(self, volume):
        """Driver entry point for destroying existing volumes.

        If the iSCSI storage is created using driver then
        iSCSI storage cancel request is raised.

        If the iSCSI storage is just liked with this volume using
        `softlayer_volume_id` while creation, then the iSCSI storage
        remains.
        """
        self.client.delete(volume)

    def _delete_metadata(self, _id):
        admin_context = context.get_admin_context()
        admin_meta = self._get_metadata(_id)
        for key in admin_meta.keys():
            db.volume_admin_metadata_delete(admin_context, _id, key)

    def ensure_export(self, context, volume):
        """Driver entry point to get the export info for an existing volume."""
        return {'provider_location': self.client.get_export(
                self.client._find_sl_vol_id(volume['id']))}

    def create_export(self, context, volume):
        """Driver entry point to get the export info for a new volume."""
        return {'provider_location': self.client.get_export(
                self.client._find_sl_vol_id(volume['id']))}

    def remove_export(self, context, volume):
        """Driver exntry point to remove an export for a volume.

        Since exporting is idempotent in this driver, we have nothing
        to do for unexporting.
        """
        LOG.debug(_("remove_export called"))

    def initialize_connection(self, volume, connector):
        """Driver entry point to attach a volume to an instance.

        Find the username and password of the iSCSI storage using
        SoftLayer Services. Then using iSCSI initiator tool discrover
        the IQL of the iSCSI storage so as to be able to attach it.
        """
        return self.client.connect(self.client._find_sl_vol_id(volume['id']))

    def terminate_connection(self, volume, connector, **kwargs):
        """Driver entry point to unattach a volume from an instance.

        Update the `notes` of the iSCSI that it's available for use.
        """
        sl_vol_id = self.client._find_sl_vol_id(volume['id'])
        self.client.setNotes(
            sl_vol_id,
            name=volume.get(
                'display_name',
                ''),
            id=volume.get(
                'id',
                ''),
            status='available')

    def create_snapshot(self, snapshot):
        """Driver entry point for creating a snapshot.

        If the iSCSI storage has space for new snapshots
        then create new snapshot. Otherwise, order
        snapshot space and then create snapshot.
        """
        volume = snapshot['volume']
        sl_vol_id = self.client._find_sl_vol_id(volume['id'])
        self.client.create_snapshot(sl_vol_id, snapshot)

    def _delete_snap_id(self, volume, snap_id):
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, volume['id'])
        if snap_id in metadata:
            del metadata[snap_id]
            db.volume_admin_metadata_update(
                admin_context, volume['id'], metadata, delete=True)

    def delete_snapshot(self, snapshot):
        """Driver entry point for deleting a snapshot."""
        volume = snapshot['volume']
        sl_vol_id = self._find_sl_snapshot_id(volume['id'], snapshot['id'])
        self.client.delete_snapshot(sl_vol_id)
        self._delete_snap_id(volume, snapshot['id'])

    def create_volume_from_snapshot(self, volume, snapshot):
        """Driver entry point for creating a new volume from a snapshot.

        Since, Snapshot is available as iSCSI storage in SoftLayer
        we copy the contents into new volume.
        """
        sl_snap_id = self._find_sl_snapshot_id(snapshot['volume']['id'],
                                               snapshot['id'])
        model_update = self._create_copy(volume, sl_snap_id)
        model_update = self._check_display_name(volume['id'], model_update)
        return model_update

    def _attch(self, conn):
        protocol = conn['driver_volume_type']
        LOG.debug("Attaching for protocol '%s'" % protocol)
        connector = utils.brick_get_connector(protocol)
        device = connector.connect_volume(conn['data'])
        host_device = device['path']
        if not connector.check_valid_device(host_device):
            #raise exception.DeviceUnavailable(device=host_device)
            raise exception.InvalidResult(
                "Unable to get valid device %s" %
                host_device)
        return {'conn': conn, 'device': device, 'connector': connector}

    def create_cloned_volume(self, volume, src_vref):
        """Creates clone of an existing volume.

        1. Create a new iSCSI storage.
        2. Attach it.
        3. Attach the source volume.
        4. Copy source into new volume.
        5. Detach both the volume.
        """
        model_update = self._create_copy(
            volume,
            self.client._find_sl_vol_id(src_vref['id']))
        model_update['source_volid'] = src_vref['id']
        model_update = self._check_display_name(volume['id'], model_update)
        return model_update

    def _create_copy(self, volume, src_sl_id):
        """Creates a clone of the specified volume."""
        model_update = self.client.create_volume(volume)
        size = volume['size']
        new_vol_sl_id = model_update['admin_meta']['id']
        LOG.debug("Created iSCSI volume. Trying to attach for coping.")
        dest_conn = self.client.connect(new_vol_sl_id)
        source_conn = self.client.connect(src_sl_id)
        LOG.debug("Got connection info for both source and the destination")
        try:
            dest_attach_info = self._attch(dest_conn)
        except Exception:
            LOG.error(
                "Unable to attach the newly created volume. Deleting it.")
            self.client.delete(volume)
            raise
        try:
            src_attach_info = self._attch(source_conn)
        except Exception:
            LOG.error(
                "Unable to attach the source volume. Deleting newly created volume.")
            self._detach_volume(dest_attach_info)
            self.client.delete(volume)
            raise
        try:
            LOG.debug(
                "Both source and destination volumes are attached successfully. Copying data.")
            size_in_mb = int(size) * 1024    # vol size is in GB
            volume_utils.copy_volume(src_attach_info['device']['path'],
                                     dest_attach_info['device']['path'],
                                     size_in_mb)
        except Exception:
            LOG.error(
                "Error while copying data. Deleting newly created volume.")
            self._detach_volume(dest_attach_info)
            self.client.delete(volume)
            raise  # TODO handle
        finally:
            try:
                self._detach_volume(dest_attach_info)
                self._detach_volume(src_attach_info)
            except:
                pass
        LOG.info("Successfully cloned the volume")
        return model_update

    def get_volume_stats(self, refresh=False):
        """Get volume status.

        Since there is no limit on the volumes created.
        Provide infinite storage available.
        """
        if refresh:
            self._update_volume_status()

        return self._stats

    def _update_volume_status(self):

        LOG.debug(_("Updating volume status"))
        data = {}
        backend_name = self.configuration.safe_get('volume_backend_name')
        data["volume_backend_name"] = backend_name or 'SoftLayer_iSCSI'
        data["vendor_name"] = 'SoftLayer'
        data["driver_version"] = '1.0'
        data["storage_protocol"] = 'iSCSI'

        data['total_capacity_gb'] = 'infinite'
        data['free_capacity_gb'] = 'infinite'
        data['reserved_percentage'] = 0
        data['QoS_support'] = False
        self._stats = data
