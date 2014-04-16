#!/usr/bin/env python


class Config:
    def call(self, name, *args, **kwargs):
        return True

    __call__ = call

    def __getattr__(self, name):
        def call_handler(*args, **kwargs):
            " Handler that actually makes the API call "
            return self(name, *args, **kwargs)
        return call_handler


cfg = Config()
