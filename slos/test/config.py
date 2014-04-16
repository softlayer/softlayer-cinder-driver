#!/usr/bin/env python


class Config(object):
    _cfg = {}

    def safe_get(self, name):
        self._cfg.get(name)

    def append_config_values(self, opts):
        pass

    def __setattr__(self, name, value):
        self._cfg[name] = value

    def __getattr__(self, name):
        return self._cfg.get(name)

    def reset_all(self):
        for key in self._cfg.keys():
            del self._cfg[key]
