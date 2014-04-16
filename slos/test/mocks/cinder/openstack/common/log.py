#!/usr/bin/env python


class FakeLogger:
    def __init__(self, name):
        self.name = name

    def info(self, string):
        pass

    def debug(self, string):
        pass

    def error(self, string):
        pass

    def warn(self, string):
        pass


def getLogger(name):
    return FakeLogger(name)
