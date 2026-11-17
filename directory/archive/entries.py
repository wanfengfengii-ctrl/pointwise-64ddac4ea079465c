import json
from directory.quality.candidate import DirectoryEntry
from directory.quality.attributes import attributes_to_mapping


def _entry(row):
    dn, object_uuid, attributes, department = row
    values = json.loads(attributes)
    return DirectoryEntry(dn, object_uuid,
                          tuple((key, tuple(values[key])) for key in sorted(values)),
                          tuple(json.loads(department)) if department is not None else None)


def write_entries(connection, snapshot_id, entries):
    connection.executemany(
        "INSERT INTO entries(snapshot_id,dn,uuid,attributes,department_path) VALUES(?,?,?,?,?)",
        ((snapshot_id, entry.dn, entry.uuid,
          json.dumps(attributes_to_mapping(entry.attributes), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")),
          json.dumps(entry.department_path, ensure_ascii=False, separators=(",", ":"))
          if entry.department_path is not None else None) for entry in entries))


def read_entry(connection, snapshot_id, dn):
    row = connection.execute(
        "SELECT dn,uuid,attributes,department_path FROM entries WHERE snapshot_id=? AND dn=?",
        (snapshot_id, dn)).fetchone()
    return _entry(row) if row is not None else None


def iter_entries(connection, snapshot_id):
    cursor = connection.execute(
        "SELECT dn,uuid,attributes,department_path FROM entries "
        "WHERE snapshot_id=? ORDER BY dn COLLATE BINARY,uuid COLLATE BINARY",
        (snapshot_id,))
    try:
        for row in cursor:
            yield _entry(row)
    finally:
        cursor.close()
