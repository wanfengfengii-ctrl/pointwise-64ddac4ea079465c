import sqlite3


class UnsupportedSchema(ValueError):
    pass


class StoreClosed(RuntimeError):
    pass


DDL = (
    """CREATE TABLE IF NOT EXISTS snapshots(
       id INTEGER PRIMARY KEY, source_id TEXT NOT NULL UNIQUE,
       input_format TEXT NOT NULL CHECK(input_format IN ('records','jsonl')),
       completed INTEGER NOT NULL CHECK(completed=1),
       record_count INTEGER NOT NULL CHECK(record_count>=0),
       digest TEXT NOT NULL, department_count INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sources(
       source_id TEXT PRIMARY KEY, snapshot_id INTEGER NOT NULL UNIQUE,
       FOREIGN KEY(snapshot_id) REFERENCES snapshots(id))""",
    """CREATE TABLE IF NOT EXISTS entries(
       snapshot_id INTEGER NOT NULL, dn TEXT NOT NULL, uuid TEXT NOT NULL,
       attributes TEXT NOT NULL, department_path TEXT,
       PRIMARY KEY(snapshot_id,dn), UNIQUE(snapshot_id,uuid),
       FOREIGN KEY(snapshot_id) REFERENCES snapshots(id))""",
    """CREATE TABLE IF NOT EXISTS departments(
       snapshot_id INTEGER NOT NULL, path TEXT NOT NULL, parent TEXT,
       PRIMARY KEY(snapshot_id,path),
       FOREIGN KEY(snapshot_id) REFERENCES snapshots(id),
       FOREIGN KEY(snapshot_id,parent) REFERENCES departments(snapshot_id,path))""",
    """CREATE TABLE IF NOT EXISTS members(
       snapshot_id INTEGER NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL,
       dn TEXT NOT NULL, CHECK(kind IN ('direct','recursive')),
       PRIMARY KEY(snapshot_id,path,kind,dn),
       FOREIGN KEY(snapshot_id,path) REFERENCES departments(snapshot_id,path),
       FOREIGN KEY(snapshot_id,dn) REFERENCES entries(snapshot_id,dn))""",
    """CREATE TABLE IF NOT EXISTS current_snapshot(
       singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       snapshot_id INTEGER NOT NULL,
       FOREIGN KEY(snapshot_id) REFERENCES snapshots(id))"""
)


def initialize_schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        raise UnsupportedSchema(f"unsupported schema version {version}")
    if connection.in_transaction:
        raise RuntimeError("initialization requires idle connection")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in DDL:
            connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


class ArchiveDatabase:
    def __init__(self, path):
        self._connection = sqlite3.connect(path, isolation_level=None)
        try:
            self._connection.execute("PRAGMA foreign_keys=ON")
            initialize_schema(self._connection)
        except BaseException:
            self._connection.close()
            self._connection = None
            raise

    @property
    def connection(self):
        if self._connection is None:
            raise StoreClosed("archive is closed")
        return self._connection

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
