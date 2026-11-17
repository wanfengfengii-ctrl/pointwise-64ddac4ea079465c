import json
from directory.membership.model import DepartmentNode, DepartmentProjection


def _path(path):
    return json.dumps(path, ensure_ascii=False, separators=(",", ":"))


def write_projection(connection, snapshot_id, projection):
    for node in sorted(projection.nodes, key=lambda n: (len(n.path), n.path)):
        connection.execute("INSERT INTO departments(snapshot_id,path,parent) VALUES(?,?,?)",
                           (snapshot_id, _path(node.path),
                            _path(node.parent) if node.parent is not None else None))
        for kind, dns in (("direct", node.direct_dns), ("recursive", node.recursive_dns)):
            connection.executemany(
                "INSERT INTO members(snapshot_id,path,kind,dn) VALUES(?,?,?,?)",
                ((snapshot_id, _path(node.path), kind, dn) for dn in dns))


def member_dns(connection, snapshot_id, path, recursive):
    return tuple(row[0] for row in connection.execute(
        "SELECT dn FROM members WHERE snapshot_id=? AND path=? AND kind=? "
        "ORDER BY dn COLLATE BINARY",
        (snapshot_id, _path(path), "recursive" if recursive else "direct")))


def read_projection(connection, snapshot_id):
    rows = connection.execute(
        "SELECT path,parent FROM departments WHERE snapshot_id=?", (snapshot_id,)).fetchall()
    parents = {tuple(json.loads(path)): tuple(json.loads(parent)) if parent else None
               for path, parent in rows}
    nodes = []
    for path in sorted(parents):
        children = tuple(p for p in sorted(parents) if parents[p] == path)
        nodes.append(DepartmentNode(path, parents[path], children,
                                    member_dns(connection, snapshot_id, path, False),
                                    member_dns(connection, snapshot_id, path, True)))
    return DepartmentProjection(tuple(nodes))
