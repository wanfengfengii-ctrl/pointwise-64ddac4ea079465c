import re
import unicodedata
from collections.abc import Mapping

ATTRIBUTE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]*", re.ASCII)


class InvalidAttributes(ValueError):
    pass


def normalize_attributes(raw):
    if not isinstance(raw, Mapping):
        raise InvalidAttributes("attributes must be a mapping")
    result = {}
    for key, values in raw.items():
        if not isinstance(key, str) or not ATTRIBUTE_NAME.fullmatch(key):
            raise InvalidAttributes("invalid attribute name")
        name = key.lower()
        if name in result:
            raise InvalidAttributes("normalized attribute name collision: " + name)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise InvalidAttributes("attribute values must be string lists")
        normalized = set()
        for value in values:
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise InvalidAttributes("attribute contains non-Unicode scalar") from exc
            normalized.add(unicodedata.normalize("NFC", value))
        result[name] = tuple(sorted(normalized))
    return tuple(sorted(result.items()))


def attributes_to_mapping(attributes):
    return {name: list(values) for name, values in attributes}
