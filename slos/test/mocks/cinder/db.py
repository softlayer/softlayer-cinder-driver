from mock import MagicMock


class Db:
    volume_get_all = MagicMock()
    volume_admin_metadata_get = MagicMock()
    snapshot_metadata_update = MagicMock()
    volume_metadata_update = MagicMock()
    volume_admin_metadata_delete = MagicMock()
    volume_metadata_get = MagicMock()
    volume_admin_metadata_update = MagicMock()

    def __init__(self):
        self.volume_admin_metadata_get.return_value = {
            'sl_id': '2',
            'billing_item_id': '2',
            'portal': '10.0.0.2',
            'capacityGb': '1',
            'username': 'foo',
            'password': 'bar'}
        self.volume_get_all.return_value = [{'id': 'i12'}]
        self.snapshot_metadata_update.return_value = None
        self.volume_metadata_update.return_value = None
        self.volume_admin_metadata_delete.return_value = None
        self.volume_metadata_get.return_value = {}
        self.volume_admin_metadata_update.return_value = None

    def reset_mocks(self):
        from mock import MagicMock
        self.volume_get_all = MagicMock()
        self.volume_admin_metadata_get = MagicMock()
        self.snapshot_metadata_update = MagicMock()
        self.volume_metadata_update = MagicMock()
        self.volume_admin_metadata_delete = MagicMock()
        self.volume_metadata_get = MagicMock()
        self.volume_admin_metadata_update = MagicMock()

        self.volume_get_all.return_value = [{'id': 'i12'}]
        self.volume_admin_metadata_get.return_value = {
            'sl_id': '2',
            'billing_item_id': '2',
            'portal': '10.0.0.2',
            'capacityGb': '1',
            'username': 'foo',
            'password': 'bar'}
        self.snapshot_metadata_update.return_value = None
        self.volume_metadata_update.return_value = None
        self.volume_admin_metadata_delete.return_value = None
        self.volume_metadata_get.return_value = {}
        self.volume_admin_metadata_update.return_value = None

import sys
sys.modules[__name__] = Db()
