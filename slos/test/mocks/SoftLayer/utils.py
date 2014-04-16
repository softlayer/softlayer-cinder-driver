#!/usr/bin/env python
import six
KNOWN_OPERATIONS = ['<=', '>=', '<', '>', '~', '!~', '*=', '^=', '$=', '_=']
string_types = six.string_types  # pylint: disable=C0103


def query_filter(query):
    """ Translate a query-style string to a 'filter'. Query can be the
    following formats:

    Case Insensitive
      'value' OR '*= value'    Contains
      'value*' OR '^= value'   Begins with value
      '*value' OR '$= value'   Ends with value
      '*value*' OR '_= value'  Contains value

    Case Sensitive
      '~ value'   Contains
      '!~ value'  Does not contain
      '> value'   Greater than value
      '< value'   Less than value
      '>= value'  Greater than or equal to value
      '<= value'  Less than or equal to value

    :param string query: query string

    """
    try:
        return {'operation': int(query)}
    except ValueError:
        pass

    if isinstance(query, string_types):
        query = query.strip()
        for operation in KNOWN_OPERATIONS:
            if query.startswith(operation):
                query = "%s %s" % (operation, query[len(operation):].strip())
                return {'operation': query}
        if query.startswith('*') and query.endswith('*'):
            query = "*= %s" % query.strip('*')
        elif query.startswith('*'):
            query = "$= %s" % query.strip('*')
        elif query.endswith('*'):
            query = "^= %s" % query.strip('*')
        else:
            query = "_= %s" % query

    return {'operation': query}


class NestedDict(dict):
    """ This helps with accessing a heavily nested dictionary. Access to keys
        which don't exist will result in a new, empty dictionary
    """

    def __getitem__(self, key):
        if key in self:
            return self.get(key)
        return self.setdefault(key, NestedDict())

    def to_dict(self):
        """
            Converts a NestedDict instance into a real dictionary. This is
            needed for places where strict type checking is done.
        """
        new_dict = {}
        for key, val in self.items():
            if isinstance(val, NestedDict):
                new_dict[key] = val.to_dict()
            else:
                new_dict[key] = val
        return new_dict
