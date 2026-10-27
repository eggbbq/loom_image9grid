"""Shared case-insensitive name exclusion policy."""
DEFAULT_IGNORE_NAMES = ('icon', 'ico', '图标')


def normalize_ignored_names(names):
    if not isinstance(names, (list, tuple)) or any(not isinstance(value, str) or not value.strip() for value in names):
        raise ValueError('ignored names must be a list of non-empty strings')
    return tuple(dict.fromkeys(value.strip().casefold() for value in names))


def matches_ignored_name(name, names):
    return any(value in str(name).casefold() for value in normalize_ignored_names(names))
