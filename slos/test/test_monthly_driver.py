#!/usr/bin/env python
import cinder.db
import copy
import SoftLayer
from SoftLayer.exceptions import SoftLayerAPIError
from cinder.exception import VolumeBackendAPIException
from mock import patch, ANY, call, MagicMock
from cinder import exception
import cinder.db as db_utils
from cinder.volume import utils as vol_utils
import cinder.utils as c_utils
from cinder.context import get_admin_context
from cinder.openstack.common import processutils as proc_utils
from slos.test import DriverTestBase


class SoftLayerDriverTestCase(DriverTestBase):

    def setUp(self):
        super(SoftLayerDriverTestCase, self).setUp()
        self.driver.do_setup(None)
        self.driver.check_for_setup_error()

    def tearDown(self):
        super(SoftLayerDriverTestCase, self).tearDown()

    def test_create_unavailable_size(self):
        SoftLayer.Client['Product_Package'].getItems.\
            return_value = []
        self.assertRaises(
            exception.VolumeBackendAPIException,
            self.driver.create_volume, self.volume)
        SoftLayer.Client['Product_Package'].getItems.assert_called_once()

    def test_volume_active_fail(self):
        SoftLayer.Client['Billing_Order_Item'].getBillingItem.return_value = {}
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_volume, self.volume)

    def test_order_fail(self):
        SoftLayer.Client['Product_Order'].verifyOrder.\
            side_effect = SoftLayerAPIError("")
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_volume, self.volume)

    def test_create_ceil_volume(self):
        SoftLayer.Client['Product_Package'].getItems.\
            return_value = [{'id': 4, 'prices': [{'id': 4}], 'capacity': '4'},
                            {'id': 3, 'prices': [{'id': 3}], 'capacity': '3'}]

        self.config.sl_vol_order_ceil = True
        self.driver.create_volume(self.volume)

        self.expected_order['prices'][0]['id'] = 3  # smallest bigger volume
        SoftLayer.Client['Product_Order'].placeOrder.assert_called_once_with(
            self.expected_order)
        SoftLayer.Client['Product_Package'].getItems.assert_called_once_with(
            id=0, filter={'items':
                          {'categories':
                              {'categoryCode': {'operation': '_= iscsi'}},
                              'capacity': {'operation': '>= 1'}}},
            mask=ANY)

    def test_volume_metadata_updated(self):
        self.config.sl_use_name = 'metadata'
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 1}, update)
        cinder.db.volume_metadata_update.called_once_with(
            ANY, self.volume['id'], {'SL_iSCSI_Name': 'foo'}, False)

    def test_volume_display_name_updated(self):
        self.config.sl_use_name = 'display_name'
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'display_name': 'foo', 'size': 1}, update)

    def assert_create_vol_params(self, volume):
        SoftLayer.Client['Product_Package'].getItems.\
            assert_called_once_with(
                id=0, filter={'items':
                              {'categories':
                               {'categoryCode':
                                {'operation': '_= iscsi'}},
                                  'capacity': {'operation': volume['size']}}},
                mask=ANY)
        SoftLayer.Client['Product_Order'].verifyOrder.assert_called_once_with(
            self.expected_order)

        SoftLayer.Client['Product_Order'].placeOrder.assert_called_once_with(
            self.expected_order)
        f = SoftLayer.Client['Account'].getIscsiNetworkStorage
        f.assert_called_once_with(
            mask=ANY, filter={'iscsiNetworkStorage':
                              {'username':
                               {'operation': '_= foo'}}})
        SoftLayer.Client['Network_Storage_Iscsi'].editObject.called_once_with({
            'notes': str({'name': self.volume['id']})})
        self.assertMetaUpdated(volume['id'])

    def test_create_volume(self):
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 1}, update)
        self.assert_create_vol_params(self.volume)

    def vol_admin_meta_get(self, admin_context, vol_id):
        return self.volume_map.get(vol_id)

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_create_cloned_volume(self, detach_vol):
        source_vol, dest_vol = self.setup_two_vols()
        self.setup_attach()

        update = self.driver.create_cloned_volume(dest_vol, source_vol)

        self.assertEquals({'size': 1, 'source_volid': 'source_vol_id'}, update)
        self.assertEquals(2, detach_vol.call_count)
        self.assertMetaUpdated(dest_vol['id'])

    def setup_two_vols(self):
        db_utils.volume_admin_metadata_get.side_effect = \
            self.vol_admin_meta_get
        self.volume_map = {}
        source_vol = 'source_vol_id'
        dest_vol = 'dest_vol_id'
        self.volume_map[source_vol] = {'sl_id': '1',
                                       'portal': '10.0.0.2',
                                       'capacityGb': '1',
                                       'username': 'foo',
                                       'password': 'bar',
                                       'billing_item_id': '1'}
        self.volume_map[dest_vol] = {'sl_id': '2',
                                     'portal': '10.0.0.2',
                                     'capacityGb': '1',
                                     'username': 'foo',
                                     'password': 'bar',
                                     'billing_item_id': '2'}
        source_vol = {
            'id': 'source_vol_id',
            'display_name': 'source_vol',
            'size': 1
        }

        dest_vol = {
            'id': 'dest_vol_id',
            'display_name': 'dest_vol',
            'size': 1
        }
        return source_vol, dest_vol

    def test_create_cloned_volume_copy_fail(self):
        source_vol, dest_vol = self.setup_two_vols()
        self.setup_initialize()
        connector = MagicMock()
        connector.connect_volume.return_value = {'path': 'valid_host'}
        connector.check_valid_device.side_effect = (True, True)
        c_utils.brick_get_connector.return_value = connector
        vol_utils.copy_volume.side_effect = proc_utils.ProcessExecutionError(
            "")

        self.assertRaises(proc_utils.ProcessExecutionError,
                          self.driver.create_cloned_volume,
                          dest_vol, source_vol)
        self.assertEquals(
            7, c_utils.execute.call_count,
            "To attach volume properly, "
            "discovery and attach should follow each other")
        SoftLayer.Client['Billing_Item'].cancelItem.\
            called_once_with(True, False, ANY, id=2)

        db_utils.volume_admin_metadata_delete.assert_has_calls(
            [call(ANY, dest_vol['id'], 'sl_id')])
        self.assertMetaUpdated(dest_vol['id'])

    def test_create_cloned_volume_old_vol_attach_fail(self):
        db_utils.volume_admin_metadata_get.side_effect = \
            self.vol_admin_meta_get
        self.volume_map = {}
        source_vol, dest_vol = self.setup_two_vols()
        self.setup_initialize()
        connector = MagicMock()
        connector.connect_volume.return_value = {'path': 'valid_host'}
        connector.check_valid_device.side_effect = (True, False)
        c_utils.brick_get_connector.return_value = connector
        self.assertRaises(exception.InvalidResults,
                          self.driver.create_cloned_volume,
                          dest_vol, source_vol)
        self.assertEquals(7, c_utils.execute.call_count,
                          "To attach volume properly, "
                          "discovery and attach should follow each other")
        SoftLayer.Client['Billing_Item'].cancelItem.\
            called_once_with(True, False, ANY, id=2)

        db_utils.volume_admin_metadata_delete.assert_has_calls(
            [call(ANY, dest_vol['id'], 'sl_id')])
        self.assertMetaUpdated(dest_vol['id'])

    def test_create_cloned_volume_new_vol_attach_fail(self):
        source_vol, dest_vol = self.setup_two_vols()
        self.setup_initialize()
        connector = MagicMock()
        connector.connect_volume.return_value = {'path': 'valid_host'}
        connector.check_valid_device.side_effect = (False, True)
        c_utils.brick_get_connector.return_value = connector
        self.assertRaises(exception.InvalidResults,
                          self.driver.create_cloned_volume,
                          dest_vol, source_vol)
        self.assertEquals(4, c_utils.execute.call_count,
                          "To attach volume properly, "
                          "discovery and attach should follow each other")
        SoftLayer.Client['Billing_Item'].cancelItem.\
            called_once_with(True, False, ANY, id=2)

        db_utils.volume_admin_metadata_delete.assert_has_calls(
            [call(ANY, dest_vol['id'], 'sl_id')])
        self.assertMetaUpdated(dest_vol['id'])

    def assertMetaUpdated(self, vol_id):
        self.assertEquals(1, db_utils.volume_admin_metadata_update.call_count)
        db_utils.volume_admin_metadata_update.assert_has_calls([
            call(ANY, vol_id, {
                'billing_item_id': '2',
                'password': 'bar',
                'username': 'foo',
                'portal': '10.0.0.2',
                'capacityGb': '1',
                'sl_id': '2'},
                False)])

    def test_note_update_fail_should_not_fail_vol_create(self):
        update = self.driver.create_volume(self.volume)
        editObject = SoftLayer.Client['Network_Storage_Iscsi'].editObject
        editObject.side_effect = SoftLayerAPIError("")
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 1}, update)

    def test_create_export_fails_when_iscsiadm_fails(self):
        c_utils.execute.return_value = ('details', 'error')
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_export,
                          None,
                          self.volume)
        self.assertEquals(c_utils.execute.call_count, 4)
        c_utils.execute.assert_has_calls([
            call('sed', '-i', '-e',
                 "/discovery.sendtargets.auth.username/d", '-e',
                 "/discovery.sendtargets.auth.password/d",
                 '/etc/iscsi/iscsid.conf',
                 run_as_root=True),
            call('sed', '-i', '-e',
                 "1idiscovery.sendtargets.auth.username = foo", "-e",
                 "1idiscovery.sendtargets.auth.password = bar",
                 '/etc/iscsi/iscsid.conf',
                 run_as_root=True),
            call('iscsiadm', '-m', 'discovery', '-t', 'st', '-p',
                 '10.0.0.2', '-o', 'new', run_as_root=True)])

    def test_ensure_export(self):
        self.setup_initialize()
        attach_details = self.driver.ensure_export(None, self.volume)
        self.assert_export(attach_details)

    def assert_export(self, attach_details):
        self.assertEquals({'provider_location':
                           '10.0.0.2:3260,1 '
                           'iqn.2001-05.com.equallogic:'
                           '0-8a0906-35b45ea0b-aa50043e7f9533bc'
                           '-ibmi278184-227'}, attach_details)
        db_utils.volume_admin_metadata_get.assert_called_once_with(
            self.fake_context, self.volume['id'])
        SoftLayer.Client['Network_Storage_Iscsi'].getObject.\
            called_once_with(id=2, mask=ANY)
        get_admin_context.called_once()

    def test_create_export(self):
        self.setup_initialize()
        attach_details = self.driver.create_export(None, self.volume)
        self.assert_export(attach_details)

    def test_remove_export(self):
        self.driver.remove_export(self.volume, None)

    def assertConnectionValue(self, connection, lun=0):
        self.assertEquals({
            'driver_volume_type': 'iscsi',
            'data': {
                'target_discovered': True,
                'encrypted': False,
                'target_iqn': 'iqn.2001-05.com.equallogic:'
                '0-8a0906-35b45ea0b-aa50043e7f9533bc-ibmi278184-227',
                'target_portal': '10.0.0.2:3260',
                'volume_id': 2,
                'target_lun': lun,
                'auth_password': 'bar',
                'auth_username': 'foo',
                'auth_method': u'CHAP'
            }
        }, connection)

    def test_initialize_connection_lun1(self):
        self.setup_initialize(lun=1)
        connection = self.driver.initialize_connection(self.volume, None)
        self.assertConnectionValue(connection, lun=1)

    def test_initialize_connection(self):
        self.setup_initialize()
        connection = self.driver.initialize_connection(self.volume, None)
        self.assertConnectionValue(connection)

    def setup_existing(self, size=1):
        iscsi = SoftLayer.Client['Network_Storage_Iscsi'].getObject.\
            return_value
        iscsi = copy.deepcopy(iscsi)
        iscsi['capacityGb'] = size
        SoftLayer.Client[
            'Network_Storage_Iscsi'].getObject.return_value = iscsi
        iscsi = db_utils.volume_admin_metadata_get.return_value
        iscsi = copy.deepcopy(iscsi)
        iscsi['capacityGb'] = size
        db_utils.volume_admin_metadata_get.return_value = iscsi

    def test_delete_volume(self):
        self.setup_existing()
        self.driver.delete_volume(self.volume)
        SoftLayer.Client['Billing_Item'].cancelItem.\
            called_once_with(True, False, ANY, id=2)
        vol_id = self.volume['id']
        db_utils.volume_admin_metadata_delete.assert_has_calls(
            [call(self.fake_context, vol_id, 'sl_id')])

    def test_non_existent_delete_succeeds(self):
        SoftLayer.Client['Network_Storage_Iscsi'].getObject.\
            side_effect = SoftLayerAPIError("")
        self.driver.delete_volume(self.volume)
        vol_id = self.volume['id']
        self.assertMetadataDeleted(vol_id)

    def assertMetadataDeleted(self, vol_id):
        db_utils.volume_admin_metadata_delete.assert_has_calls(
            [call(self.fake_context, vol_id, 'sl_id'),
             call(self.fake_context, vol_id, 'billing_item_id'),
             call(self.fake_context, vol_id, 'portal'),
             call(self.fake_context, vol_id, 'capacityGb'),
             call(self.fake_context, vol_id, 'password'),
             call(self.fake_context, vol_id, 'username')],
            any_order=True)

    def test_create_snapshot(self):
        self.setup_existing(size=2)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        update = self.driver.create_snapshot(snapshot)
        db_utils.volume_admin_metadata_update.\
            assert_called_once_with(ANY,
                                    self.volume['id'],
                                    {'os-snap-id': 'sl-snap-id'}, False)
        self.assertIsNone(update)
        f = SoftLayer.Client['Product_Order'].placeOrder
        f.called_once_with({
            'complexType':
            'SoftLayer_Container_Product_Order_'
            'Network_Storage_Iscsi_SnapshotSpace',
            'location': 1234,
            'packageId': 0,
            'prices': [{'id': 2}],
            'quantity': 1,
            'volumeId': 2})
        self.assertIsNone(update)

    def test_1_gb_snapshot_create_fails(self):
        self.setup_existing()
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          snapshot)

    def test_insufficient_existing_snap_space(self):
        self.setup_existing(size=4)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.config.sl_order_snap_space = True
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        f.side_effect = [SoftLayerAPIError("Insufficient snapshot reserve "
                                           "space to create a snapshot "
                                           "for the volume"),
                         {'username': 'test_snapshot', 'id': 'sl-snap-id'}]
        self.config.sl_order_snap_space = True
        f = SoftLayer.Client['Product_Package'].getItems
        f.return_value = [{'id': 2, 'prices': [{'id': 2}], 'capacity': '2'}]
        getObject = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        iscsi = getObject.return_value
        iscsi['snapshotCapacityGb'] = 1
        iscsi_with_snap_space = copy.deepcopy(iscsi)
        iscsi_with_snap_space['snapshotCapacityGb'] = 5
        f = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        f.side_effect = [iscsi, iscsi_with_snap_space, iscsi_with_snap_space]
        self.config.sl_snap_space_active_retry = 4
        self.config.sl_snap_space_active_wait = 0
        self.driver.create_snapshot(snapshot)

    def test_snap_space_allocation_fails(self):
        self.setup_existing(size=2)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.config.sl_order_snap_space = True
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        f.side_effect = [SoftLayerAPIError("Insufficient snapshot reserve "
                                           "space to create a snapshot "
                                           "for the volume"),
                         {'username': 'test_snapshot', 'id': 'sl-snap-id'}]
        f = SoftLayer.Client['Product_Package'].getItems
        f.return_value = []
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          snapshot)

    def test_snap_order_fails(self):
        self.setup_existing(size=2)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.config.sl_order_snap_space = True
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        f.side_effect = [SoftLayerAPIError("Insufficient snapshot reserve "
                                           "space to create a snapshot "
                                           "for the volume"),
                         {'username': 'test_snapshot', 'id': 'sl-snap-id'}]
        f = SoftLayer.Client['Product_Package'].getItems
        f.return_value = [{'id': 2, 'prices': [{'id': 2}], 'capacity': '2'}]
        getObject = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        iscsi = getObject.return_value
        iscsi_with_snap_space = copy.deepcopy(iscsi)
        iscsi_with_snap_space['snapshotCapacityGb'] = 2
        f = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        f.side_effect = [iscsi]
        self.config.sl_snap_space_active_retry = 4
        self.config.sl_snap_space_active_wait = 0
        f = SoftLayer.Client['Product_Order'].placeOrder
        f.side_effect = SoftLayerAPIError("")
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          snapshot)

    def test_snap_space_unavailable(self):
        self.setup_existing(size=2)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.config.sl_order_snap_space = True
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        f.side_effect = [SoftLayerAPIError("Insufficient snapshot reserve "
                                           "space to create a snapshot "
                                           "for the volume"),
                         {'username': 'test_snapshot', 'id': 'sl-snap-id'}]
        f = SoftLayer.Client['Product_Package'].getItems
        f.return_value = [{'id': 2, 'prices': [{'id': 2}], 'capacity': '2'}]
        getObject = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        iscsi = getObject.return_value
        f = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        f.return_value = iscsi
        self.config.sl_snap_space_active_retry = 4
        self.config.sl_snap_space_active_wait = 0
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          snapshot)

    def test_snap_space_allocated_when_required(self):
        self.setup_existing(size=2)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.config.sl_order_snap_space = True
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        f.side_effect = [SoftLayerAPIError("Insufficient snapshot reserve "
                                           "space to create a snapshot "
                                           "for the volume"),
                         {'username': 'test_snapshot', 'id': 'sl-snap-id'}]
        f = SoftLayer.Client['Product_Package'].getItems
        f.return_value = [{'id': 2, 'prices': [{'id': 2}], 'capacity': '2'}]
        getObject = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        iscsi = getObject.return_value
        iscsi_with_snap_space = copy.deepcopy(iscsi)
        iscsi_with_snap_space['snapshotCapacityGb'] = 2
        f = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        f.side_effect = [iscsi, iscsi_with_snap_space, iscsi]
        self.config.sl_snap_space_active_retry = 4
        self.config.sl_snap_space_active_wait = 0
        update = self.driver.create_snapshot(snapshot)
        db_utils.volume_admin_metadata_update.\
            assert_called_once_with(ANY,
                                    self.volume['id'],
                                    {'os-snap-id': 'sl-snap-id'}, False)
        f = SoftLayer.Client['Product_Order'].placeOrder
        f.called_once_with({
            'complexType':
            'SoftLayer_Container_Product_Order_'
            'Network_Storage_Iscsi_SnapshotSpace',
            'location': 1234,
            'packageId': 0,
            'prices': [{'id': 2}],
            'quantity': 1,
            'volumeId': 2})
        self.assertIsNone(update)

    def test_snapshot_non_existent_delete(self):
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.setup_existing(size=2)
        db_utils.volume_admin_metadata_get.return_value = {}
        self.assertRaises(exception.InvalidSnapshot,
                          self.driver.delete_snapshot, snapshot)

    def test_sl_error_on_delete(self):
        self.setup_existing(size=2)
        db_utils.volume_admin_metadata_get.return_value = {'os-snap-id': 4234}
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        deleteObject = SoftLayer.Client['Network_Storage_Iscsi'].deleteObject
        deleteObject.side_effect = SoftLayerAPIError("Non_existent")
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.delete_snapshot, snapshot)

    def test_snapshot_delete(self):
        self.setup_existing(size=2)
        db_utils.volume_admin_metadata_get.return_value = {'os-snap-id': 4234}
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.driver.delete_snapshot(snapshot)
        deleteObject = SoftLayer.Client['Network_Storage_Iscsi'].deleteObject
        deleteObject.called_once_with(id=4234)
        db_utils.volume_admin_metadata_delete.called_once_with(
            self.fake_context, 'os-snap-id')

    def test_snap_space_disabled_fails(self):
        self.setup_existing(size=2)
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        self.config.sl_order_snap_space = False
        f = SoftLayer.Client['Network_Storage_Iscsi'].createSnapshot
        f.side_effect = SoftLayerAPIError("Insufficient snapshot reserve "
                                          "space to create a snapshot "
                                          "for the volume")
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_snapshot,
                          snapshot)

    def test_create_volume_from_snapshot(self):
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        new_vol = copy.deepcopy(self.volume)
        new_vol['size'] = 2
        new_vol['id'] = 'new-os-vol'
        vol_data = db_utils.volume_admin_metadata_get.return_value
        vol_with_snap = copy.deepcopy(vol_data)
        vol_with_snap['os-snap-id'] = 34234
        db_utils.volume_admin_metadata_get.return_value = vol_with_snap
        self.driver.create_volume_from_snapshot(new_vol, snapshot)
        f = SoftLayer.Client['Network_Storage_Iscsi'].restoreFromSnapshot
        f.assert_called_once_with(34234, id=2)
        self.assert_create_vol_params(new_vol)

    def test_create_volume_from_snapshot_fails(self):
        snapshot = {'id': 'os-snap-id', 'volume': self.volume}
        new_vol = copy.deepcopy(self.volume)
        new_vol['size'] = 2
        new_vol['id'] = 'new-os-vol'
        vol_data = db_utils.volume_admin_metadata_get.return_value
        vol_with_snap = copy.deepcopy(vol_data)
        vol_with_snap['os-snap-id'] = 34234
        db_utils.volume_admin_metadata_get.side_effect = \
            [vol_with_snap, vol_with_snap, vol_data, vol_data, vol_data]
        f = SoftLayer.Client['Network_Storage_Iscsi'].restoreFromSnapshot
        f.side_effect = SoftLayerAPIError("Failed to restore")
        self.assertRaises(VolumeBackendAPIException,
                          self.driver.create_volume_from_snapshot,
                          new_vol,
                          snapshot)
        SoftLayer.Client['Billing_Item'].cancelItem.\
            assert_called_once_with(True, False, ANY, id=2)
        self.assertMetaUpdated('new-os-vol')
        self.assertMetadataDeleted('new-os-vol')

    def test_no_billing_delete_succeeds(self):
        iscsi = SoftLayer.Client[
            'Network_Storage_Iscsi'].getObject.return_value
        iscsi = copy.deepcopy(iscsi)
        del iscsi['billingItem']
        SoftLayer.Client[
            'Network_Storage_Iscsi'].getObject.return_value = iscsi
        vol_id = self.volume['id']
        self.driver.delete_volume(self.volume)
        vol_id = self.volume['id']
        self.assertMetadataDeleted(vol_id)
