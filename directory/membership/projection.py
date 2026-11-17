from directory.quality.candidate import validate_candidate
from directory.membership.model import DepartmentNode, DepartmentProjection, ProjectionError
from directory.membership.hierarchy import build_hierarchy, is_ancestor
from directory.membership.compute import compute_direct_members, compute_recursive_members


def validate_projection(candidate, projection):
    if not isinstance(projection, DepartmentProjection) or not isinstance(projection.nodes, tuple):
        raise ProjectionError("projection is not sealed")
    paths = {entry.department_path[:n]
             for entry in candidate.entries if entry.department_path is not None
             for n in range(1, len(entry.department_path) + 1)}
    if tuple(node.path for node in projection.nodes) != tuple(sorted(paths)):
        raise ProjectionError("projection topology differs from directory")
    for node in projection.nodes:
        parent = node.path[:-1] if len(node.path) > 1 else None
        children = tuple(sorted(p for p in paths if len(p) == len(node.path) + 1
                                and p[:-1] == node.path))
        direct = tuple(sorted(e.dn for e in candidate.entries
                              if e.department_path == node.path))
        recursive = tuple(sorted(e.dn for e in candidate.entries
                                 if e.department_path is not None
                                 and is_ancestor(node.path, e.department_path)))
        if (node.parent, node.children, node.direct_dns, node.recursive_dns) != (
                parent, children, direct, recursive):
            raise ProjectionError("projection membership or relationships differ")
    return projection


def build_projection(candidate):
    validate_candidate(candidate)
    hierarchy = build_hierarchy(candidate.entries)
    direct = compute_direct_members(candidate.entries, hierarchy)
    recursive = compute_recursive_members(direct, hierarchy)
    nodes = tuple(DepartmentNode(path, path[:-1] if len(path) > 1 else None,
                                hierarchy[path], tuple(sorted(direct[path])),
                                tuple(sorted(recursive[path])))
                  for path in sorted(hierarchy))
    return validate_projection(candidate, DepartmentProjection(nodes))
