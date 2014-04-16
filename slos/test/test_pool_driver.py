import copy
import cinder.db as db_utils
import cinder.utils as c_utils

from cinder import exception
from cinder.volume import utils as vol_utils
from cinder.openstack.common.processutils import ProcessExecutionError

from mock import call, patch, ANY

import SoftLayer
from SoftLayer.exceptions import SoftLayerAPIError

from . import DriverTestBase


class SoftLayerPoolDriverTestCase(DriverTestBase):

    def setUp(self):
        super(SoftLayerPoolDriverTestCase, self).setUp()
        from slos.cinder.driver.iscsi import SoftLayerISCSIPoolDriver
        self.driver = SoftLayerISCSIPoolDriver(configuration=self.config,
                                               db=self.db)
        self.driver.do_setup(None)
        self.driver.check_for_setup_error()

    def test_create_volume_from_metadata(self):
        db_utils.volume_get_all.return_value = \
            [{'id': 'some-vol-which-is-no-imported'}]
        db_utils.volume_admin_metadata_get.return_value = {}
        db_utils.volume_metadata_get.return_value = \
            {'softlayer_volume_id': '2'}
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 1}, update)
        db_utils.volume_admin_metadata_update.\
            assert_called_once_with(self.fake_context, self.volume['id'], {
                'sl_id': '2',
                'username': 'foo',
                'portal': '10.0.0.2',
                'capacityGb': '1',
                'billing_item_id': '2',
                'password': 'bar'
            }, False)
        place_order = SoftLayer.Client['Product_Order'].placeOrder
        self.assertEquals(0, place_order.call_count)

    def test_existing_size_mismatch_fails(self):
        db_utils.volume_get_all.return_value = []
        db_utils.volume_admin_metadata_get.return_value = {}
        db_utils.volume_metadata_get.return_value = \
            {'softlayer_volume_id': '2'}
        getObject = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        sl_vol = copy.deepcopy(getObject.return_value)
        sl_vol['capacityGb'] = 20
        getObject.return_value = sl_vol
        self.assertRaises(exception.InvalidVolume,
                          self.driver.create_volume, self.volume)

    def test_ceil_create(self):
        self.config.sl_vol_order_ceil = True
        f = SoftLayer.Client['Account'].getIscsiNetworkStorage
        f.return_value = [
            {'id': 20,
             'capacityGb': 20,
             'username': 'foo20',
             'serviceResourceBackendIpAddress': '10.0.0.2',
             'password': 'bar20',
             'billingItem': {'id': 20}},
            {'id': 40,
             'capacityGb': 40,
             'username': 'foo40',
             'password': 'bar40',
             'serviceResourceBackendIpAddress': '10.0.0.2',
             'billingItem': {'id': 40}},
        ]
        getObject = SoftLayer.Client['Network_Storage_Iscsi'].getObject
        getObject.return_value = f.return_value[0]
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 20}, update)
        db_utils.volume_admin_metadata_update.called_once_with(
            self.fake_context,
            self.volume['id'],
            {'sl_id': '20',
             'username': 'foo20',
             'password': 'bar20',
             'billing_item_id': '20',
             'portal': '10.0.0.2',
             'capacityGb': '20'
             }
        )

    def test_create_in_use_fails(self):
        db_utils.volume_metadata_get.return_value = \
            {'softlayer_volume_id': '2'}
        self.assertRaises(exception.InvalidVolume,
                          self.driver.create_volume, self.volume)

    def test_use_non_existing_fails(self):
        db_utils.volume_metadata_get.return_value = \
            {'softlayer_volume_id': '3'}
        SoftLayer.Client['Network_Storage_Iscsi'].getObject.side_effect =\
            SoftLayerAPIError("Object Doesn't Exists")
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume, self.volume)

    def test_create_from_pool(self):
        db_utils.volume_get_all.return_value = []
        db_utils.volume_admin_metadata_get.return_value = {}
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 1}, update)
        db_utils.volume_admin_metadata_update.\
            assert_called_once_with(self.fake_context, self.volume['id'], {
                'sl_id': '2',
                'billing_item_id': '2',
                'portal': '10.0.0.2',
                'capacityGb': '1',
                'username': 'foo',
                'password': 'bar'
            }, False)
        place_order = SoftLayer.Client['Product_Order'].placeOrder
        self.assertEquals(0, place_order.call_count)
        SoftLayer.Client['Account'].getIscsiNetworkStorage.\
            assert_called_once_with(filter={'iscsiNetworkStorage': {
                'billingItem': {'location': {'id': {'operation': 1234}}},
                'capacityGb': {'operation': 1}}},
                mask=ANY)

    def test_pool_full_create_fails(self):
        self.config.sl_pool_real_order = False
        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume, self.volume)

    def test_pool_full_create_orders(self):
        self.config.sl_pool_real_order = True
        f = SoftLayer.Client['Product_Package'].getItems
        f.return_value = [{'id': 2, 'prices': [{'id': 2}], 'capacity': '1'}]
        db_utils.volume_get_all.return_value = [{'id': '123'}]
        update = self.driver.create_volume(self.volume)
        self.assertEquals({'size': 1}, update)
        SoftLayer.Client['Product_Order'].placeOrder.\
            assert_called_once_with(self.expected_order)
        db_utils.volume_admin_metadata_update.\
            assert_called_once_with(self.fake_context, self.volume['id'], {
                'sl_id': '2',
                'billing_item_id': '2',
                'portal': '10.0.0.2',
                'capacityGb': '1',
                'username': 'foo',
                'password': 'bar'
            }, False)

    def test_invalid_size_request_fails(self):
        self.config.sl_pool_real_order = True
        # no volumes in the pool initially
        SoftLayer.Client['Account'].getIscsiNetworkStorage.return_value = []
        db_utils.volume_get_all.return_value = []

        # invalid size returns zero items
        SoftLayer.Client['Product_Package'].getItems.return_value = []

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.driver.create_volume,
                          self.volume)

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_invalid_vol_clear_config(self, detach_volume):
        self.config.sl_pool_volume_clear = 'invalid'
        self.assertRaises(exception.InvalidConfigurationValue,
                          self.driver.delete_volume,
                          self.volume)
        db_utils.volume_admin_metadata_delete.\
            called_once_with(self.fake_context,
                             self.volume['id'],
                             'sl_id')

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_delete_removes_from_pool(self, detach_volume):
        self.setup_attach()
        self.config.sl_pool_volume_clear = 'none'
        self.driver.delete_volume(self.volume)
        self.assertEquals(c_utils.execute.call_count, 4)
        db_utils.volume_admin_metadata_delete.\
            assert_has_calls(
                [call(self.fake_context,
                      self.volume['id'],
                      'sl_id')],
                any_order=True)
        self.assert_single_detach(detach_volume)

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_delete_zeroed_out(self, detach_volume):
        self.setup_attach()
        self.config.sl_pool_volume_clear = 'zero'
        self.driver.delete_volume(self.volume)
        vol_utils.copy_volume.assert_called_once_with('/dev/zero',
                                                      'valid_host',
                                                      1024)
        db_utils.volume_admin_metadata_delete.\
            assert_has_calls(
                [call(self.fake_context,
                      self.volume['id'],
                      'sl_id')],
                any_order=True)
        self.assert_single_detach(detach_volume)

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_delete_shreded(self, detach_volume):
        self.setup_attach()
        self.config.sl_pool_volume_clear = 'shred'
        self.driver.delete_volume(self.volume)
        self.assertEquals(5, c_utils.execute.call_count)
        c_utils.execute.assert_any_call('shred', '-n3',
                                        '-s%dMiB' % (1024,),
                                        'valid_host',
                                        run_as_root=True)
        self.assert_single_detach(detach_volume)

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_zero_out_fails(self, detach_volume):
        self.setup_attach()
        self.config.sl_pool_volume_clear = 'zero'
        vol_utils.copy_volume.side_effect = ProcessExecutionError()
        self.assertRaises(
            ProcessExecutionError, self.driver.delete_volume, self.volume)
        self.assert_single_detach(detach_volume)

    @patch('cinder.volume.driver.ISCSIDriver._detach_volume')
    def test_shred_out_fails(self, detach_volume):
        self.setup_attach()
        self.config.sl_pool_volume_clear = 'shred'
        no_error = c_utils.execute.return_value

        c_utils.execute.side_effect = \
            [no_error,  # remove creds from iscsid.conf
             no_error,  # insert new creds
             no_error,  # run iscsiadm
             ProcessExecutionError("")]  # raise at 'shred'
        self.assertRaises(
            ProcessExecutionError, self.driver.delete_volume, self.volume)
        self.assert_single_detach(detach_volume)
