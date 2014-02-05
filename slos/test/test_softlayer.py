#!/usr/bin/env python
from cinder import test
from slos.cinder.driver.iscsi import SoftLayerISCSIDriver
from cinder.keymgr import conf_key_mgr
from cinder.volume import configuration as conf
import cinder
from cinder import db
from cinder import exception
import SoftLayer
import mox
import os.path

class TestSoftLayerDriver(test.TestCase):

    def setUp(self):
        super(TestSoftLayerDriver, self).setUp()
        self.stubs.Set(os.path, 'exists', lambda _: True)
        self.config = mox.MockObject(conf.Configuration)
        self.db = mox.MockObject(db)
        self.config.append_config_values(mox.IgnoreArg())

    def test_simple(self):
        self.assertTrue(os.path.exists('foo'))

    def test_invalid_strategy(self):
        self.config.sl_strategy = 'unknown'
        driver = SoftLayerISCSIDriver(configuration=self.config, db=self.db)
        self.assertRaises(exception.InvalidConfigurationValue, driver.do_setup, None)

    def test_invalid_credentials_throws_error(self):
        self.config.sl_strategy = 'monthly'
        self.config.sl_username = 'foo'
        self.config.sl_api_key = 'bar'
        self.config.sl_datacenter = 'Dallas 5'
        self.stubs.Set(cinder.utils, 'execute', lambda *args, **kwarfs: True)
        self.sl_client = self.mox.CreateMockAnything(SoftLayer.Client)
        self.sl_client(username=self.config.sl_username, api_key=self.config.sl_api_key).AndReturn(self.sl_client)
        self.sl_client['Product_Order'].AndReturn(self.sl_client)
        self.sl_client['Location_Datacenter'].AndReturn(self.sl_client)
        self.sl_client.getDatacenters(mask='mask[longName,id]').AndReturn([{'longName' : 'Dallas 5', 'id' : 1}])
        self.mox.ReplayAll()
        self.stubs.Set(SoftLayer, 'Client', self.sl_client)
        driver = SoftLayerISCSIDriver(configuration=self.config, db=self.db)
        driver.do_setup(None)
        driver.check_for_setup_error()
        self.mox.VerifyAll()
        
