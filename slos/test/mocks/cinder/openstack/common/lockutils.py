#!/usr/bin/env python
import functools


def synchronized(name, lock_file_prefix, external=False, lock_path=None):
    def wrap(f):
        @functools.wraps(f)
        def inner(*args, **kwargs):
            return f(*args, **kwargs)
        return inner

    return wrap
