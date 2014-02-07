import string
import time
import cinder.utils as utils
import SoftLayer

from SoftLayer.utils import query_filter, NestedDict
from SoftLayer.exceptions import SoftLayerAPIError

from cinder import db
from cinder import context
from cinder.openstack.common import log as logging
from cinder.exception import (
    VolumeBackendAPIException, InvalidSnapshot, InvalidInput, InvalidResult)
from cinder.openstack.common import lockutils

LOG = logging.getLogger(__name__)


class SLClient(object):

    def __init__(self, configuration={}, parent=None):
        self.configuration = configuration
        self.client = SoftLayer.Client(username=configuration.sl_username,
                                       api_key=self.configuration.sl_api_key)
        self.parent = parent
        self.product_order = self.client['Product_Order']

    def check_dc(self):
        datacenters = self.client['Location_Datacenter'].getDatacenters(
            mask='mask[longName,id]')
        for dc in datacenters:
            if dc['longName'] == self.configuration.sl_datacenter:
                self.location = dc['id']
                return
        err_msg = (_('Invalid username password and datacenter '
                     'combination. Valid usename and api_key'
                     ' along with datacenter location must be specified.'))
        raise InvalidInput(reason=err_msg)

    def _update(self, _id, admin_meta):
        admin_context = context.get_admin_context()
        db.volume_admin_metadata_update(admin_context, _id, admin_meta, False)

    def find_items(self, size):
        items = []
        _filter = NestedDict({})
        _filter[
            'itemPrices'][
            'item'][
            'description'] = query_filter(
            '~GB iSCSI SAN Storage')
        if self.configuration.sl_vol_order_ceil:
            _filter['itemPrices']['item'][
                'capacity'] = query_filter('>=%s' % size)
        else:
            _filter['itemPrices']['item']['capacity'] = query_filter(size)
        iscsi_item_prices = self.client['Product_Package'].getItemPrices(
            id=0,
            mask='mask[id,recurringFee,item[capacity]]',
            filter=_filter.to_dict())
        iscsi_item_prices = sorted(
            iscsi_item_prices,
            key=lambda x: float(x.get('recurringFee', 0)))
        iscsi_item_prices = sorted(
            iscsi_item_prices,
            key=lambda x: float(x['item']['capacity']))
        for price in iscsi_item_prices:
            items.append(price['id'])
        return items

    def create_updates(self, sl_vol_id):
        sl_vol = self.find_volume(
            sl_vol_id,
            mask='mask[id,capacityGb,username,password,billingItem[id]]')
        meta_update = {'id': sl_vol['id'],
                       'billing_item_id': sl_vol['billingItem']['id'],
                       'username': sl_vol['username'],
                       'password': sl_vol['password']}
        model_update = {'size': sl_vol['capacityGb'],
                        'display_name': sl_vol['username']}
        return (meta_update, model_update)

    def use_existing(self, name, sl_vol_id):
        sl_vol_id = int(sl_vol_id)
        sl_vol = self.find_volume(
            sl_vol_id, mask='mask=[username,password,id,capacityGb]')
        admin_meta = {}
        admin_meta['username'] = sl_vol['username']
        admin_meta['password'] = sl_vol['password']
        admin_meta['id'] = sl_vol_id
        os_model = {}
        os_model['size'] = sl_vol['capacityGb']
        os_model['display_name'] = sl_vol['username']
        self.setNotes(sl_vol_id, name=name)
        if int(model_update['size']) == int(size):
            # User has request volume of same size of the id specified.
            return (admin_meta, model_update)
        # size mismatch
        raise exception.InvalidVolume(
            reason="Requested SL volume (%s) size doesn't match."
            " SL size %s, requested size %s" % (admin_meta['id'], model_update['size'], size))

    def create_volume(self, volume):
        LOG.debug(
            _("Create volume called with name: %s, size: %s, id: %s" %
              (volume['display_name'], volume['size'], volume['id'])))
        items = self.find_items(volume['size'])
        if len(items) == 0:
            LOG.error(_("No item found for size %s" % volume['size']))
            raise VolumeBackendAPIException(
                data="iSCSI storage of %s size is not supported" %
                size)
        LOG.debug(_("%d items found for size %s" % (len(items), volume['size'])))
        return self.order_iscsi(items, volume)

    def order_iscsi(self, items, volume):
        for item in items:
            admin_meta = {}
            iscsi_order = self.build_order(item, volume['display_name'])
            try:
                self.product_order.verifyOrder(iscsi_order)
                LOG.debug(_("Order verified successfully"))
                order = self.product_order.placeOrder(iscsi_order)
            except Exception as e:
                LOG.debug(_("Cannot place order: %s" % e))
                continue
            LOG.debug(_("Order placed successfully"))
            billing_item_id = order['placedOrder']['items'][0]['id']
            LOG.debug(_("Billing item id: %s associated" % billing_item_id))
            retry = self.configuration.sl_vol_active_retry
            billingOrderItemService = self.client['Billing_Order_Item']
            billing_item = billingOrderItemService.getBillingItem(
                id=billing_item_id)
            while True:
                if retry == 0:
                    raise VolumeBackendAPIException(
                        data="Unable to retrive the billing item for the order placed. Order Id: %s" %
                        order['id'])
                retry = retry - 1
                LOG.debug(_("Sleeping for 10 sec"))
                time.sleep(10)
                billing_item = billingOrderItemService.getBillingItem(
                    id=billing_item_id)
                if not isinstance(billing_item, type({})):
                    continue
                if not billing_item.get('notes'):
                    continue
                break
            LOG.debug(_("Billing Item associated: '%s'" % billing_item))
            user_name = billing_item['notes']
            _filter = NestedDict({})
            _filter[
                'iscsiNetworkStorage'][
                'username'] = query_filter(
                user_name)
            result = self.client['Account'].getIscsiNetworkStorage(
                mask='mask[id,capacityGb]',
                filter=_filter.to_dict())
            sl_vol_id = result[0]['id']
            admin_meta, os_model = self.create_updates(sl_vol_id)
            self.setNotes(sl_vol_id, name=volume['id'])
            self._update(volume['id'], admin_meta)
            os_model['admin_meta'] = admin_meta
            return os_model
        raise VolumeBackendAPIException(
            data="Item for given size cannot be found")

    def setNotes(self, sl_vol_id, **notes):
        try:
            self.client['Network_Storage_Iscsi'].editObject(
                {'notes': str(notes)},
                id=int(sl_vol_id))
        except:
            LOG.error(_("Unable to edit notes for the iSCSI %s" % sl_vol_id))

    def build_order(self, item, name):
        order = {
            'complexType':
            'SoftLayer_Container_Product_Order_Network_Storage_Iscsi',
            'location': self.location,  # DELLA 5
            'packageId': 0,  # storage package
            'prices': [{'id': int(item)}],  # 1GB iSCSI storage
            'quantity': 1
        }
        return order

    def find_volume(self, sl_vol_id, mask='mask[billingItem[id]]'):
        try:
            return (
                self.client['Network_Storage_Iscsi'].getObject(
                    id=int(sl_vol_id),
                    mask=mask)
            )
        except Exception:
            raise VolumeBackendAPIException(
                data='Softlayer volume id %s did not found' %
                sl_vol_id)

    def _attch(self, conn):
        protocol = conn['driver_volume_type']
        LOG.debug("Attaching for protocol '%s'" % protocol)
        connector = utils.brick_get_connector(protocol)
        device = connector.connect_volume(conn['data'])
        host_device = device['path']
        if not connector.check_valid_device(host_device):
            #raise exception.DeviceUnavailable(device=host_device)
            raise InvalidResult("Unable to get valid device %s" % host_device)
        return {'conn': conn, 'device': device, 'connector': connector}

    def _get_metadata(self, _id):
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, _id)
        return metadata

    def _delete_metadata(self, _id):
        admin_context = context.get_admin_context()
        admin_meta = self._get_metadata(_id)
        for key in admin_meta.keys():
            db.volume_admin_metadata_delete(admin_context, _id, key)

    def get_sl_volume(self, volume, mask='mask[id,capacityGb,username,password,billingItem[id]]'):
        sl_id = self._find_sl_vol_id(volume['id'])
        return self.find_volume(sl_id, mask)

    def delete(self, volume):
        "Raises ticket to cancel volume"
        sl_vol = self.get_sl_volume(volume)
        if not sl_vol:
            LOG.warn("Corresponding volume for %s did not found. Assumming already deleted." % volume['id'])
            return
        self.setNotes(
            sl_vol['id'],
            status="DELETED. No Billing Item is associated "
                "with this volume. Will be invisible after few hours.")
        if not sl_vol.get('billingItem'):
            LOG.error(
                _("Billing Item not found for the volume. Assumeing volume is already canceled."))
            return
        billingItemId = sl_vol['billingItem']['id']
        self.client['SoftLayer_Billing_Item'].cancelItem(
            True,
            False,
            "No longer needed",
            id=billingItemId)
        self._delete_metadata(volume['id'])

    @lockutils.synchronized('getdetails', 'cinder-', False)
    def get_details(self, sl_vol):
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
        out, err = utils.execute('iscsiadm', '-m', 'discovery', '-t', 'st', '-p',
                                 sl_vol['serviceResourceBackendIpAddress'], '-o', 'new', run_as_root=True)
        if err and len(err) != 0:
            raise VolumeBackendAPIException(
                data="Error while 'discovery' on iSCSI details. %s" % err)
        return out

    def build_data(self, details, sl_vol):
        result = details.split(' ')
        dict = {}
        dict['driver_volume_type'] = 'iscsi'
        properties = {}
        properties['target_discovered'] = False
        properties['encrypted'] = False
        properties['target_iqn'] = string.strip(result[1])
        properties['target_portal'] = string.strip(result[0].split(',')[0])
        properties['volume_id'] = sl_vol['id']
        try:
            properties['target_lun'] = int(result[2])
        except (IndexError, ValueError):
            properties['target_lun'] = 0

        properties['auth_password'] = sl_vol['password']
        properties['auth_username'] = sl_vol['username']
        properties['auth_method'] = u'CHAP'
        dict['data'] = properties
        return dict

    def get_export(self, sl_vol_id):
        sl_vol = self.find_volume(sl_vol_id)
        details = self.get_details(sl_vol)
        return details

    def _find_sl_vol_id(self, vol_id):
        admin_context = context.get_admin_context()
        metadata = db.volume_admin_metadata_get(admin_context, vol_id)
        return metadata.get('id', None)

    def find_space(self, size):
        _filter = NestedDict({})
        _filter[
            'itemPrices'][
            'item'][
            'description'] = query_filter(
            '~iSCSI SAN Snapshot Space')
        _filter['itemPrices']['item']['capacity'] = query_filter('>=%s' % size)
        item_prices = self.client['Product_Package'].getItemPrices(
            id=0,
            mask='mask[id,item[capacity]]',
            filter=_filter.to_dict())
        LOG.debug(_("Finding item prices"))
        item_prices = sorted(
            item_prices,
            key=lambda x: int(x['item']['capacity']))
        LOG.debug(_("Item prices found: %s" % item_prices))
        if len(item_prices) == 0:
            return None
        return item_prices[0]['id']

    def increase_snapshot_space(self, sl_vol_id, capacity):
        item_price = self.find_space(capacity)
        if not item_price:
            raise VolumeBackendAPIException(
                data="Snapshot space having size %s not found." % capacity)
        snapshotSpaceOrder = {
            'complexType':
            'SoftLayer_Container_Product_Order_Network_Storage_Iscsi_SnapshotSpace',
            'location': self.location,
            'packageId': 0,
            'prices': [{'id': item_price}],
            'quantity': 1,
            'volumeId': sl_vol_id}
        try:
            self.product_order.verifyOrder(snapshotSpaceOrder)
            LOG.debug(_("Order verified successfully"))
            order = self.product_order.placeOrder(snapshotSpaceOrder)
        except Exception as e:
            LOG.debug(_("Cannot place order: %s" % e))
            raise VolumeBackendAPIException(data=e.message)

    def waitForSpace(self, sl_vol_id, retryLimit, currentCapacity, sleep=5):
        noSpace = True
        while retryLimit and noSpace:
            time.sleep(5)
            vol = self.client['Network_Storage_Iscsi'].getObject(
                id=sl_vol_id,
                mask='mask[snapshotCapacityGb]')
            newCapacity = int(vol.get('snapshotCapacityGb', '0'))
            noSpace = newCapacity <= currentCapacity
            retryLimit = retryLimit - 1
        if not retryLimit:
            raise VolumeBackendAPIException(
                data="Unable to reserve space for volume.")

    def space_needed(self, message):
        return 'Insufficient snapshot reserve space to create a snapshot for the volume' in message

    def create_snapshot(self, sl_vol_id, snapshot):
        vol = self.find_volume(sl_vol_id,
                               mask='mask[capacityGb,snapshotCapacityGb]')
        if vol['capacityGb'] == 1:
            raise VolumeBackendAPIException(
                data="1 GB Snapshot is not supported")
        try:
            sl_snapshot = self.client['Network_Storage_Iscsi'].createSnapshot(
                '',
                id=sl_vol_id)
        except SoftLayerAPIError as e:
            LOG.error(_("Unable to create snapshot of the given volume."))
            if self.space_needed(e.message) and self.configuration.sl_order_snap_space:
                LOG.info(
                    _("Increasing the snapshot space of the softlayer volume %s" % sl_vol_id))
                currentCapacity = vol.get('snapshotCapacityGb', 0)
                if currentCapacity == 0:
                    self.increase_snapshot_space(sl_vol_id, vol['capacityGb'])
                else:
                    self.increase_snapshot_space(
                        sl_vol_id,
                        currentCapacity +
                        1)
                retryLimit = 30
                self.waitForSpace(sl_vol_id, retryLimit, currentCapacity)
                # space increased try creating snapshot again.
                return self.create_snapshot(sl_vol_id, snapshot)
            else:
                raise VolumeBackendAPIException(
                    data="Unable to create snapshot. %s" % e.message)
        model_update = {}
        if sl_snapshot.get('username', None):
            model_update['display_name'] = sl_snapshot.get('username', '')
        meta_update = {}
        meta_update[snapshot['id']] = sl_snapshot.get('id')
        self._update(snapshot['volume']['id'], meta_update)
        return model_update

    def delete_snapshot(self, snap_id):
        if not snap_id:
            raise InvalidSnapshot(
                "Snapshot not fond on the SoftLayer account.")
        try:
            self.client[
                'SoftLayer_Network_Storage_Iscsi'].deleteObject(
                id=int(snap_id))
        except Exception as e:
            LOG.error(_("%s" % e.message))
            raise VolumeBackendAPIException(_("%s" % e.message))

    def connect(self, sl_vol_id):
        details = self.get_export(sl_vol_id)
        sl_vol = self.find_volume(sl_vol_id)
        self.setNotes(sl_vol_id, os_status='in-use')
        return self.build_data(details, sl_vol)
