#!/usr/bin/env python


class BaseException(Exception):
    def __init__(self, message=None, **kwargs):
        pass


class InvalidResults(BaseException):
    pass


class InvalidConfigurationValue(BaseException):
    pass


class VolumeBackendAPIException(BaseException):
    pass


class InvalidSnapshot(BaseException):
    pass


class InvalidVolume(BaseException):
    pass


class InvalidInput(BaseException):
    pass
