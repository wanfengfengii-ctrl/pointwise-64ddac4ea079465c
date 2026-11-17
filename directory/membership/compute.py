from directory.membership.model import ProjectionError


def compute_direct_members(entries, hierarchy):
    direct = {path: set() for path in hierarchy}
    for entry in entries:
        path = entry.department_path
        if path is not None:
            if path not in direct:
                raise ProjectionError("missing department node")
            direct[path].add(entry.dn)
    return direct


def compute_recursive_members(direct, hierarchy):
    if set(direct) != set(hierarchy):
        raise ProjectionError("topology and direct sets differ")
    recursive = {}
    for path in sorted(hierarchy, key=lambda p: (-len(p), p)):
        result = set(direct[path])
        for child in hierarchy[path]:
            if child not in recursive or child[:-1] != path:
                raise ProjectionError("invalid child relationship")
            result.update(recursive[child])
        recursive[path] = result
    return recursive
