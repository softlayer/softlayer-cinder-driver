from slos.cinder.driver.iscsi import SoftLayerISCSIDriver
from slos.test import DriverTestBase


class TestSoftLayerOtherMethods(DriverTestBase):

    def setUp(self):
        super(TestSoftLayerOtherMethods, self).setUp()
        self.volume = {'id': 'vol-id',
                       'size': 1}
        self.config.sl_datacenter = 'dal05'
        self.driver = SoftLayerISCSIDriver(configuration=self.config, db=None)
        self.driver.do_setup(None)
        self.driver.check_for_setup_error()

    def test_get_volume_stat(self):
        stats = self.driver.get_volume_stats(refresh=True)
        result = {
            'volume_backend_name': 'SoftLayer_iSCSI',
            'vendor_name': 'SoftLayer',
            'driver_version': '1.0',
            'storage_protocol': 'iSCSI',
            'total_capacity_gb': 'infinite',
            'free_capacity_gb':  'infinite',
            'reserved_percentage': 0,
            'QoS_support': False,
        }
        self.assertEquals(result, stats)
        stats = self.driver.get_volume_stats(refresh=False)
        self.assertEquals(result, stats)

    def test_terminate_connection(self):
        self.driver.terminate_connection(self.volume, None)
