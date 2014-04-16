from mock import MagicMock
import sys


class Utils(object):

    def __init__(self):
        self.execute = MagicMock()
        self.brick_get_connector = MagicMock()
        self.execute.return_value = ("", "")
        self.brick_get_connector.return_value = None

    def reset_mocks(self):
        from mock import MagicMock
        self.execute = MagicMock()
        self.brick_get_connector = MagicMock()
        self.execute.return_value = ("", "")
        self.brick_get_connector.return_value = None

sys.modules[__name__] = Utils()
