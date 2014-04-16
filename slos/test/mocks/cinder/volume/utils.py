#!/usr/bin/python
from mock import MagicMock


class Utils:
    copy_volume = MagicMock()

    def reset_mocks(self):
        from mock import MagicMock
        self.copy_volume = MagicMock()

import sys
sys.modules[__name__] = Utils()
