# vim: tabstop=4 shiftwidth=4 softtabstop=4


import sys
import os
import unittest

mock_modules = os.path.abspath(os.path.join(__file__, '..', 'mocks'))

sys.path.insert(0, mock_modules)
# See http://code.google.com/p/python-nose/issues/detail?id=373
# The code below enables nosetests to work with i18n _() blocks
import __builtin__
setattr(__builtin__, '_', lambda x: x)
import SoftLayer
import cinder.db
import cinder.context
import cinder.utils
import cinder.volume.utils as volume_utils
from mock import MagicMock, ANY
from slos.test import config
from slos.cinder.driver.iscsi import SoftLayerISCSIDriver


class DriverTestBase(unittest.TestCase):

    def setUp(self):
        self.config = config.Config()
        self.config.reset_all()
        self.db = object()
        self.fake_context = 'admincntx'
        self.config.sl_datacenter = 'dal05'
        self.config.sl_vol_active_retry = 4
        self.config.sl_use_name = None
        self.config.sl_vol_active_wait = 0
        self.driver = \
            SoftLayerISCSIDriver(
                configuration=self.config,
                db=self.db)

        self.volume = {
            'id': 'abcdefab-cdef-abcd-efab-cdefabcdefab',
            'display_name': 'test_volume',
            'size': 1
        }
        self.expected_order = {
            'complexType': 'SoftLayer_Container_'
            'Product_Order_Network_'
            'Storage_Iscsi',

            'location': 1234,
            'packageId': 0,
            'prices': [{'id': 2}],
            'quantity': 1
        }

    def setup_initialize(self, lun=0):
        iqn = '10.0.0.2:3260,1 '\
              'iqn.2001-05.com.equallogic:'\
              '0-8a0906-35b45ea0b-aa50043e7f9533bc-'\
              'ibmi278184-227'
        if lun != 0:
            iqn = '%s %d' % (iqn, lun)

        cinder.utils.execute.return_value = (iqn, '')

    def setup_attach(self):
        self.setup_initialize()
        connector = MagicMock()
        connector.connect_volume.return_value = {'path': 'valid_host'}
        connector.check_valid_device.return_value = True
        cinder.utils.brick_get_connector.return_value = connector

    def assert_single_detach(self, detach_volume):
        detach_volume.assert_called_once_with({
            'connector': ANY,
            'conn': ANY,
            'device': {'path': 'valid_host'}
            })

    def tearDown(self):
        self.config.reset_all()
        cinder.db.reset_mocks()
        cinder.context.reset_mocks()
        cinder.utils.reset_mocks()
        volume_utils.reset_mocks()
        SoftLayer.Client.reset_mock()
