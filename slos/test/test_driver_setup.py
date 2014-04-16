from slos.cinder.driver.iscsi import SoftLayerISCSIDriver
from cinder import exception
from cinder.openstack.common import processutils as proc_utils
from mock import patch
from slos.test import DriverTestBase


class TestSoftLayerDriverSetUp(DriverTestBase):

    """
    Tests related to verifying errors during driver set-up
    """

    def setUp(self):
        super(TestSoftLayerDriverSetUp, self).setUp()

    def test_invalid_location(self):
        self.config.sl_datacenter = 'unknown'
        driver = \
            SoftLayerISCSIDriver(
                configuration=self.config,
                db=self.db)
        driver.do_setup(None)
        self.assertRaises(
            exception.InvalidInput,
            driver.check_for_setup_error)

    @patch('cinder.utils.execute')
    def test_root_sed_execute(self, execute):
        execute.side_effect = proc_utils.ProcessExecutionError("")

        self.config.sl_datacenter = 'Dallas 5'
        driver = \
            SoftLayerISCSIDriver(
                configuration=self.config,
                db=self.db)
        driver.do_setup(None)
        self.assertRaises(
            proc_utils.ProcessExecutionError,
            driver.check_for_setup_error)
