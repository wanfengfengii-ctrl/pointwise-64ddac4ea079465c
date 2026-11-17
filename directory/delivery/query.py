from directory.quality.names import normalize_dn
from directory.archive.entries import read_entry


def get_entry(store, snapshot_id, dn):
    store.require(snapshot_id)
    name = normalize_dn(dn)
    return read_entry(store.connection, snapshot_id, name)
