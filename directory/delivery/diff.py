from dataclasses import dataclass
from directory.archive.entries import iter_entries


@dataclass(frozen=True)
class DiffItem:
    kind: str
    dn: str
    before: object
    after: object


def diff_snapshots(store, before, after):
    store.require(before)
    store.require(after)
    old = {entry.dn: entry for entry in iter_entries(store.connection, before)}
    new = {entry.dn: entry for entry in iter_entries(store.connection, after)}
    result = []
    for dn in sorted(old.keys() | new.keys()):
        left, right = old.get(dn), new.get(dn)
        if left == right:
            continue
        kind = "added" if left is None else "removed" if right is None else "modified"
        result.append(DiffItem(kind, dn, left, right))
    return result
