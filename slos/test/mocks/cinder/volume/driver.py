

class ISCSIDriver(object):

    def __init__(self, *args, **kwargs):
        self.configuration = kwargs.get('configuration')

    def _detach_volume(self, *args, **kwargs):
        pass
