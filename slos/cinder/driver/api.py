"""
Contains the Utilities required by SoftLayer Driver
"""
import copy
import string
import time

import cinder.exception as exception
import cinder.utils as utils

from cinder import db
from cinder import context
from cinder.openstack.common.gettextutils import _
from cinder.openstack.common import lockutils
from cinder.openstack.common import log as logging

import SoftLayer
from SoftLayer.exceptions import SoftLayerAPIError
from SoftLayer.utils import query_filter, NestedDict

LOG = logging.getLogger(__name__)


class MetadataManager(object):
    """
    Manages the admin metadata representation of
    SoftLayer volumes.
    """

    def _local_volume_references(self, cntx):
        """
        Get IDs of all volumes in Cinder.
        """
        all_vols = db.volume_get_all(
            cntx, marker=None, limit=None,
            sort_key='created_at', sort_dir='desc')
        return [vol['id'] for vol in all_vols]

    def all_imported(self):
        """
        Returns ID of all imported volumes
        :returns: list of external vol's id
        """
        cntx = context.get_admin_context()
        local_volumes = self._local_volume_references(cntx)
        sl_volumes = []
        for vol in local_volumes:
            sl_vol = self.deserialize(vol)
            if not sl_vol:
                continue
            sl_volumes.append(sl_vol['id'])
        return sl_volumes

    def get_all(self, vol_id):
        """
        Retrives the user metadata of the volume.

        :param vol_id: OpenStack Volume ID.
        """
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, vol_id)
        return metadata

    def deserialize(self, vol_id):
        """
        Convertes the database representation of the volume
        into SoftLayer Volume object
        """
        meta = copy.deepcopy(self.get_all(vol_id))
        if 'sl_id' not in meta:
            return None
        for int_field in ('sl_id', 'billing_item_id', 'capacityGb'):
            meta[int_field] = int(meta[int_field])
        meta['billingItem'] = {'id': meta.get('billing_item_id')}
        meta['serviceResourceBackendIpAddress'] = meta['portal']
        meta['id'] = meta['sl_id']
        del meta['billing_item_id']
        del meta['portal']
        del meta['sl_id']
        return meta

    def serialize(self, vol_id, sl_vol):
        """
        Converts and stores the SoftLayer volume object
        into database as admin metadata.
        """
        defaults = {
            'sl_id': str(sl_vol['id']),
            'billing_item_id': str(sl_vol['billingItem']['id']),
            'portal': sl_vol['serviceResourceBackendIpAddress'],
            'capacityGb': str(sl_vol['capacityGb']),
            'username': sl_vol['username'],
            'password': sl_vol['password'],
        }
        self.update_meta(vol_id, defaults)

    def delete_all(self, vol_id):
        """
        Delete the admin_metadata created for given volume.

        :param vol_id: OpenStack Volume ID.

        """
        admin_context = context.get_admin_context()
        admin_meta = self.get_all(vol_id)
        for key in admin_meta.keys():
            db.volume_admin_metadata_delete(admin_context, vol_id, key)

    def delete_entry(self, volume, entry):
        """
        Remove snapshot's ID from the volume's admin metadata

        :param volume: OpenStack Volume Object.
        :param entry: OpenStack Volume ID.
        """
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, volume['id'])
        if entry in metadata:
            del metadata[entry]
            db.volume_admin_metadata_update(
                admin_context, volume['id'], metadata, delete=True)

    def update_meta(self, _id, admin_meta):
        """
        Update the admin metadata
        """
        admin_context = context.get_admin_context()
        db.volume_admin_metadata_update(
            admin_context, _id, admin_meta, False)

    def get(self, vol_id, entry):
        """
        Finds the corresponding SoftLayer SnapshotID of
        given OpenStack Snapshot.

        :param vol_id: OpenStack Volume ID.

        """
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, vol_id)
        return metadata.get(entry, None)

    def get_user_meta(self, vol_id):
        """
        Retrive the user metadata of the volume.

        :param vol_id: OpenStack Volume ID.
        """
        admin_context = context.get_admin_context()
        metadata = db.volume_metadata_get(admin_context, vol_id)
        return metadata

    def update_user_meta(self, vol_id, metadata, delete=False):
        """
        Update the user metadata of the given volume

        :param vol_id: OpenStack volume ID.
        :param metadata: dict containing metadata to be updated
        :param delete: True if update should result in deletion of existing
        """
        admin_context = context.get_admin_context()
        db.volume_metadata_update(admin_context, vol_id,
                                  metadata, delete)


class IscsiVolumeManager(object):

    """
    Cinder Driver for SoftLayer helper module
    """

    def __init__(self, configuration={}):
        self.configuration = configuration
        self.client = SoftLayer.Client(
            username=configuration.sl_username,
            api_key=self.configuration.sl_api_key)
        self.product_order = self.client['Product_Order']
        self.location = None

    def check_dc(self):
        """
        Varify the datacenter name in config.
        """
        datacenters = self.client['Location_Datacenter'].getDatacenters(
            mask='mask[name,id]')
        for datacenter in datacenters:
            if datacenter['name'] == self.configuration.sl_datacenter:
                self.location = datacenter['id']
                return
        err_msg = (_('Invalid username password and datacenter '
                     'combination. Valid usename and api_key'
                     ' along with datacenter location must be specified.'))
        raise exception.InvalidInput(reason=err_msg)

    def _find_item(self, size, category_code, ceil):
        """
        Find the item_price IDs for the iSCSIs of given size

        :param int size: iSCSI volume size
        :returns: Returns a list of item price IDs matching
                  the given volume size or first large enough size, if the
                 `sl_vol_order_ceil` configuration value is se
        """
        _filter = NestedDict({})
        _filter[
            'items'][
            'categories'][
            'categoryCode'] = query_filter(category_code)
        if ceil:
            _filter['items'][
                'capacity'] = query_filter('>=%s' % size)
        else:
            _filter['items']['capacity'] = query_filter(size)
        iscsi_item_prices = self.client['Product_Package'].getItems(
            id=0,
            mask=','.join(('id', 'prices', 'capacity')),
            filter=_filter.to_dict())
        if len(iscsi_item_prices) == 0:
            return None
        iscsi_item_prices = sorted(
            iscsi_item_prices,
            key=lambda x: float(x['capacity']))

        return iscsi_item_prices[0]['prices'][0]['id']

    def create_volume(self, volume):
        """
        Creates a new volume on the SoftLayer account.

        :param volume: OpenStack Volume Object.
        :returns: returns admin metadata and volume model_update which
                  can be used by the driver and manager respectively
                  to update volume's information.
        """
        LOG.debug(
            _("Create volume called with name: %s, size: %s, id: %s" %
              (volume['display_name'], volume['size'], volume['id'])))
        item = self._find_item(volume['size'],
                               'iscsi',
                               self.configuration.sl_vol_order_ceil)
        if not item:
            LOG.error(_("No item found for size %s" % volume['size']))
            raise exception.VolumeBackendAPIException(
                data="iSCSI storage of %s size is not supported" %
                volume['size'])
        return self._order_iscsi(item)

    def _order_iscsi(self, item):
        """
        Places an order for volume.

        :param item: item price id to be used to order
        """
        iscsi_order = self._build_order(item)
        try:
            self.product_order.verifyOrder(iscsi_order)
            LOG.debug(_("Order verified successfully"))
            order = self.product_order.placeOrder(iscsi_order)
        except SoftLayerAPIError as ex:
            LOG.debug(_("Cannot place order: %s" % ex))
            raise exception.VolumeBackendAPIException(data=ex.message)
        LOG.debug(_("Order placed successfully"))
        billing_item_id = order['placedOrder']['items'][0]['id']
        LOG.debug(_("Billing item id: %s associated" % billing_item_id))
        billing_svc = self.client['Billing_Order_Item']
        for retry in xrange(self.configuration.sl_vol_active_retry):
            billing_item = billing_svc.getBillingItem(
                id=billing_item_id)
            if billing_item and \
                    billing_item.get('notes'):
                # iscsi is available
                break
            LOG.debug("Ordered volume is not in active state, "
                      "sleeping after %s retries" % retry)
            time.sleep(self.configuration.sl_vol_active_wait)

        if not billing_item.get('notes'):
            raise exception.VolumeBackendAPIException(
                data="Unable to retrive the "
                "billing item for the order placed. "
                "Order Id: %s" %
                order.get('id'))
        LOG.debug(_("Billing Item associated: '%s'" % billing_item))
        user_name = billing_item['notes']
        _filter = NestedDict({})
        _filter[
            'iscsiNetworkStorage'][
            'username'] = query_filter(
            user_name)
        result = self.client['Account'].\
            getIscsiNetworkStorage(mask='mask[billingItem[id]]',
                                   filter=_filter.to_dict())
        sl_vol = result[0]
        return sl_vol

    def _build_order(self, item):
        """
        Build order structure required by placeOrder

        :param: int item: item price ID to be ordered
        :returns: the dict required by the `placeOrder`
        """
        order = {
            'complexType':
            'SoftLayer_Container_Product_Order_Network_Storage_Iscsi',
            'location': self.location,  # DELLA 5
            'packageId': 0,  # storage package
            'prices': [{'id': int(item)}],  # 1GB iSCSI storage
            'quantity': 1
        }
        return order

    def _get_vol(self, sl_vol_id, mask='mask[billingItem[id]]'):
        """
        Search the SoftLayer volume object using ID

        :param sl_vol_id: SoftLayer iSCSI volume ID.
        :param mask: fields from the volume to be retrived
        """
        try:
            return (
                self.client['Network_Storage_Iscsi'].getObject(
                    id=int(sl_vol_id),
                    mask=mask)
            )
        except SoftLayerAPIError:
            raise exception.VolumeBackendAPIException(
                data='Softlayer volume id %s did not found' %
                sl_vol_id)

    def cancel(self, sl_vol):
        """
        Cancels a given iSCSI target.
        """
        billing_item_id = sl_vol['billingItem']['id']
        self.client['Billing_Item'].cancelItem(
            True,
            False,
            "No longer needed",
            id=billing_item_id)

    @lockutils.synchronized('run_iscsiadm', 'cinder-', False)
    def run_iscsiadm(self, sl_vol):
        """
        Run `iscsiadm` command on SoftLayer iSCSI target
        to fetch IQN and other details of the target.
        """
        # modify the iscsid.conf
        # first remove and then insert, makes sure we always will have new
        # values placed, even if there isn't any existing values.
        utils.execute('sed', '-i', '-e',
                      "/discovery.sendtargets.auth.username/d", '-e',
                      "/discovery.sendtargets.auth.password/d",
                      '/etc/iscsi/iscsid.conf',
                      run_as_root=True)
        utils.execute('sed', '-i', '-e',
                      "1idiscovery.sendtargets.auth.username = %s" % sl_vol[
                          'username'], '-e',
                      "1idiscovery.sendtargets.auth.password = %s" % sl_vol[
                          'password'],
                      '/etc/iscsi/iscsid.conf',
                      run_as_root=True)
        (out, err) = utils.execute(
            'iscsiadm', '-m', 'discovery', '-t', 'st', '-p',
            sl_vol['serviceResourceBackendIpAddress'],
            '-o', 'new', run_as_root=True)
        if err and len(err) != 0:
            raise exception.VolumeBackendAPIException(
                data="Error while 'discovery' on iSCSI details. %s" % err)
        return out

    def _create_properties(self, iscsi_detail, sl_vol):
        """
        Build properties data from the volume detail.
        """
        result = iscsi_detail.split(' ')
        data = {}
        data['driver_volume_type'] = 'iscsi'
        properties = {}
        properties['target_discovered'] = True
        properties['encrypted'] = False
        properties['target_iqn'] = string.strip(result[1])
        properties['target_portal'] = string.strip(result[0].split(',')[0])
        properties['volume_id'] = sl_vol['id']
        if len(result) > 2:
            properties['target_lun'] = int(result[2])
        else:
            properties['target_lun'] = 0

        properties['auth_password'] = sl_vol['password']
        properties['auth_username'] = sl_vol['username']
        properties['auth_method'] = u'CHAP'
        data['data'] = properties
        return data

    def increase_snapshot_space(self, sl_vol_id, capacity):
        """
        Increase the snapshot space to given capacity
        """
        item_price = self._find_item(capacity, 'iscsi_snapshot_space', True)
        if not item_price:
            raise exception.VolumeBackendAPIException(
                data="Snapshot space having size %s not found." % capacity)
        snap_space_order = {
            'complexType':
            'SoftLayer_Container_Product_Order_'
            'Network_Storage_Iscsi_SnapshotSpace',
            'location': self.location,
            'packageId': 0,
            'prices': [{'id': item_price}],
            'quantity': 1,
            'volumeId': sl_vol_id}
        try:
            self.product_order.verifyOrder(snap_space_order)
            LOG.debug(_("Order verified successfully"))
            self.product_order.placeOrder(snap_space_order)
        except SoftLayerAPIError as ex:
            LOG.debug(_("Cannot place order: %s" % ex.message))
            raise exception.VolumeBackendAPIException(data=ex.message)

    def _wait_for_space(self,
                        sl_vol_id,
                        retry_limit,
                        current_capacity, sleep=5):
        """
        Wait for snapshot space to become available.
        """
        space_allocated = False
        for retry in xrange(retry_limit):
            vol = self.client['Network_Storage_Iscsi'].getObject(
                id=sl_vol_id,
                mask='mask[snapshotCapacityGb]')
            new_capacity = int(vol.get('snapshotCapacityGb', '0'))
            space_allocated = new_capacity > current_capacity
            if space_allocated:
                break
            LOG.debug("Snapshot space not activated, "
                      "sleeping after %s retries" % retry)
            time.sleep(sleep)
        if not space_allocated:
            raise exception.VolumeBackendAPIException(
                data="Unable to reserve space for volume.")

    def space_needed(self, message):
        """
        Check if the error message from SoftLayer is
        related to insufficent space.
        """
        return \
            'Insufficient snapshot reserve space to ' \
            'create a snapshot for the volume' in message

    def create_snapshot(self, sl_vol, snapshot):
        """
        Create snapshot for volume, if required inflate the snapshot space.
        """
        sl_vol = self._get_vol(sl_vol['id'])
        if sl_vol['capacityGb'] == 1:
            raise exception.VolumeBackendAPIException(
                data="1 GB Snapshot is not supported")
        try:
            sl_snapshot = self.client['Network_Storage_Iscsi'].createSnapshot(
                '',
                id=sl_vol['id'])
        except SoftLayerAPIError as ex:
            if not self.space_needed(ex.message) or \
                    not self.configuration.sl_order_snap_space:
                LOG.error(_("Unable to create snapshot of the given volume."))
                raise exception.VolumeBackendAPIException(
                    data="Unable to create snapshot. %s" % ex.message)

            LOG.info(
                _("Increasing the snapshot space "
                  "of the softlayer volume %s" % sl_vol['id']))
            current_capacity = sl_vol.get('snapshotCapacityGb', 0)
            if current_capacity == 0:
                self.increase_snapshot_space(sl_vol['id'],
                                             sl_vol['capacityGb'])
            else:
                self.increase_snapshot_space(
                    sl_vol['id'],
                    current_capacity +
                    1)
            retry_limit = self.configuration.sl_snap_space_active_retry
            sleep = self.configuration.sl_snap_space_active_wait
            self._wait_for_space(sl_vol['id'],
                                 retry_limit,
                                 current_capacity,
                                 sleep=sleep)
            # space increased try creating snapshot again.
            return self.create_snapshot(sl_vol, snapshot)
        return sl_snapshot

    def restore_snapshot(self, sl_snap_id, sl_volume):
        """
        restore the volume to snapshot state
        """
        self.client['Network_Storage_Iscsi'].\
            restoreFromSnapshot(sl_snap_id,
                                id=sl_volume['id'])

    def delete_snapshot(self, snap_id):
        """
        Delete the snapshot
        """
        if not snap_id:
            raise exception.InvalidSnapshot(
                "Snapshot not fond on the SoftLayer account.")
        try:
            self.client[
                'Network_Storage_Iscsi'].deleteObject(
                id=int(snap_id))
        except SoftLayerAPIError as ex:
            LOG.error(_("%s" % ex.message))
            raise exception.VolumeBackendAPIException(_("%s" % ex.message))

    def get_iscsi_properties(self, sl_vol):
        """
        Get the attachment details of the volume.
        """
        iscsi_detail = self.run_iscsiadm(sl_vol)
        return self._create_properties(iscsi_detail, sl_vol)

    def find_free_volume(self, size, imported):
        """
        Find a volume in the pool of the given size.

        :param size: size to search for.
        :param imported: list of imported volumes

        :returns: sl_vol: SoftLayer iSCSI volume representation
        """
        _filter = NestedDict({})
        if self.configuration.sl_vol_order_ceil:
            _filter['iscsiNetworkStorage'][
                'capacityGb'] = query_filter('>=%s' % size)
        else:
            _filter['iscsiNetworkStorage']['capacityGb'] = query_filter(size)
        _filter['iscsiNetworkStorage'][
            'billingItem'][
            'location'][
            'id'] = query_filter(self.location)
        sl_volumes = self.client['Account'].getIscsiNetworkStorage(
            mask='mask[id,capacityGb,'
            'username,password,billingItem[id]]',
            filter=_filter.to_dict())
        if len(sl_volumes) == 0:
            return None
        sl_volumes = sorted(sl_volumes, key=lambda x: int(x['capacityGb']))
        for sl_vol in sl_volumes:
            if sl_vol['id'] in imported:
                continue
            return self._get_vol(sl_vol['id'])
        LOG.warn(_("No free volume found of size %s" % size))
        return None

    def use_exiting(self, size, sl_vol_id):
        """
        Checks if given SL volume can be used

        :param size: required volume size.
        :param sl_vol_id: SoftLayer iSCSI volume ID.

        :returns: sl_vol: SoftLayer iSCSI volume representation
        """
        sl_vol = self._get_vol(sl_vol_id)
        if int(sl_vol['capacityGb']) == int(size):
            # User has request volume of same size of the id specified.
            return sl_vol
        # size mismatch
        raise exception.InvalidVolume(
            reason="Requested SL volume (%s) size doesn't match."
            " SL size %s, requested size %s" %
            (sl_vol['id'], sl_vol['capacityGb'], size))
