from mock import MagicMock


class Context:
    get_admin_context = MagicMock()
    get_admin_context.return_value = 'admincntx'

    def reset_mocks(self):
        from mock import MagicMock
        self.get_admin_context = MagicMock()
        self.get_admin_context.return_value = 'admincntx'

import sys
sys.modules[__name__] = Context()
