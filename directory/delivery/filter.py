from directory.quality.names import normalize_department_path, InvalidDepartmentPath
from directory.archive.memberships import member_dns
from directory.archive.entries import read_entry


def members(store, snapshot_id, department_path, recursive=True):
    store.require(snapshot_id)
    path = normalize_department_path(department_path)
    if path is None:
        raise InvalidDepartmentPath("query requires a department")
    if type(recursive) is not bool:
        raise ValueError("recursive must be bool")
    dns = member_dns(store.connection, snapshot_id, path, recursive)
    result = [read_entry(store.connection, snapshot_id, dn) for dn in dns]
    if any(entry is None for entry in result):
        raise RuntimeError("dangling membership")
    return sorted(result, key=lambda entry: (entry.dn, entry.uuid))
