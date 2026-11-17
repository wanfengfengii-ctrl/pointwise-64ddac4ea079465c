from dataclasses import dataclass
from directory.source.model import SourceEvidence
from directory.quality.candidate import validate_candidate
from directory.membership.projection import validate_projection
from directory.archive.entries import write_entries, iter_entries
from directory.archive.memberships import write_projection


class UnknownSnapshot(LookupError):
    pass


class NoCurrentSnapshot(LookupError):
    pass


class SourceConflict(ValueError):
    pass


class InvalidPublication(ValueError):
    pass


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: int
    source_id: str
    input_format: str
    completed: bool
    record_count: int
    digest: str
    department_count: int


class SnapshotStore:
    def __init__(self, database):
        self.database = database

    @property
    def connection(self):
        return self.database.connection

    @staticmethod
    def _info(row):
        return SnapshotInfo(row[0], row[1], row[2], bool(row[3]), row[4], row[5], row[6])

    def require(self, snapshot_id):
        if type(snapshot_id) is not int or snapshot_id < 1:
            raise UnknownSnapshot(snapshot_id)
        row = self.connection.execute("SELECT * FROM snapshots WHERE id=?",
                                      (snapshot_id,)).fetchone()
        if row is None:
            raise UnknownSnapshot(snapshot_id)
        return self._info(row)

    def current_id(self):
        row = self.connection.execute(
            "SELECT snapshot_id FROM current_snapshot WHERE singleton=1").fetchone()
        if row is None:
            raise NoCurrentSnapshot("no published snapshot")
        return self.require(row[0]).snapshot_id

    def find_source(self, source_id):
        row = self.connection.execute(
            "SELECT snapshot_id FROM sources WHERE source_id=?", (source_id,)).fetchone()
        return self.require(row[0]) if row else None

    def list(self):
        return tuple(self._info(row) for row in self.connection.execute(
            "SELECT * FROM snapshots ORDER BY id"))

    def publish(self, candidate, projection, evidence):
        validate_candidate(candidate)
        validate_projection(candidate, projection)
        if (not isinstance(evidence, SourceEvidence) or not evidence.completed
                or evidence.record_count != candidate.count):
            raise InvalidPublication("source evidence does not match candidate")
        connection = self.connection
        if connection.in_transaction:
            raise InvalidPublication("publication requires idle connection")
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.find_source(evidence.descriptor.source_id)
            if existing is not None:
                if (existing.digest != candidate.digest
                        or tuple(iter_entries(connection, existing.snapshot_id)) != candidate.entries):
                    raise SourceConflict(evidence.descriptor.source_id)
                connection.commit()
                return existing
            cursor = connection.execute(
                "INSERT INTO snapshots(source_id,input_format,completed,record_count,digest,"
                "department_count) VALUES(?,?,?,?,?,?)",
                (evidence.descriptor.source_id, evidence.descriptor.input_format, 1,
                 candidate.count, candidate.digest, len(projection.nodes)))
            snapshot_id = cursor.lastrowid
            write_entries(connection, snapshot_id, candidate.entries)
            write_projection(connection, snapshot_id, projection)
            connection.execute("INSERT INTO sources(source_id,snapshot_id) VALUES(?,?)",
                               (evidence.descriptor.source_id, snapshot_id))
            self._set_current(snapshot_id)
            info = self.require(snapshot_id)
            connection.commit()
            return info
        except BaseException:
            connection.rollback()
            raise

    def _set_current(self, snapshot_id):
        self.connection.execute(
            "INSERT INTO current_snapshot(singleton,snapshot_id) VALUES(1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id",
            (snapshot_id,))
