import uuid


class InvalidUUID(ValueError):
    pass


class DuplicateDN(ValueError):
    pass


class DuplicateUUID(ValueError):
    pass


def normalize_uuid(values):
    if len(values) != 1 or not isinstance(values[0], str):
        raise InvalidUUID("objectUUID requires exactly one value")
    try:
        return str(uuid.UUID(values[0]))
    except (ValueError, AttributeError) as exc:
        raise InvalidUUID("invalid objectUUID") from exc


class IdentityIndex:
    def __init__(self):
        self.dns = set()
        self.uuids = set()

    def claim(self, dn, object_uuid):
        if dn in self.dns:
            raise DuplicateDN(dn)
        if object_uuid in self.uuids:
            raise DuplicateUUID(object_uuid)
        self.dns.add(dn)
        self.uuids.add(object_uuid)

    def clear(self):
        self.dns.clear()
        self.uuids.clear()
