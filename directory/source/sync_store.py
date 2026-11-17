"""Persistent state for serial LDAP refresh collection (SYNC).

The tables added here keep the per-source opaque resume cookie together with
the V1 archive pointer. Every publication produced by SyncCollector is a full
V1 snapshot (snapshots/entries/departments/members plus the single-row current
pointer); the sync table only owns the cookie, an internal source label and a
publication sequence used to build unique V1 source ids. All writes happen in
exactly one SQLite transaction together with the directory content.
"""
from directory.archive.entries import iter_entries, write_entries
from directory.archive.memberships import write_projection
from directory.quality.candidate import Candidate, content_digest
from directory.source.model import InvalidSource

SYNC_DDL = (
    """CREATE TABLE IF NOT EXISTS sync_sources(
       source_id TEXT PRIMARY KEY,
       snapshot_id INTEGER NOT NULL,
       cookie BLOB NOT NULL,
       publication_seq INTEGER NOT NULL CHECK(publication_seq>=0),
       FOREIGN KEY(snapshot_id) REFERENCES snapshots(id))""",
)


def initialize_sync_schema(connection):
    """Create missing SYNC structures; idempotent and safe over V1 data."""
    if connection.in_transaction:
        raise RuntimeError("initialization requires idle connection")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in SYNC_DDL:
            connection.execute(statement)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def validate_source_id(source_id):
    if not isinstance(source_id, str) or not source_id:
        raise InvalidSource("source_id must be a nonempty string")
    return source_id


class SyncStore:
    """Thin persistence helper; owns no open transactions between calls."""

    def __init__(self, database):
        self.database = database
        initialize_sync_schema(database.connection)

    @property
    def connection(self):
        return self.database.connection

    def load_state(self, source_id):
        """Return (published entries tuple, cookie, snapshot_id) for reopen.

        A source without a SYNC checkpoint seeds from the shared current V1
        pointer; an empty library is an empty directory with no cookie. The
        snapshot id lets the next publication compare against the exact prior
        content rather than whatever the current pointer happens to hold.
        """
        row = self.connection.execute(
            "SELECT snapshot_id,cookie FROM sync_sources WHERE source_id=?",
            (source_id,)).fetchone()
        if row is None:
            from directory.archive.snapshots import SnapshotStore
            try:
                snapshot_id = SnapshotStore(self.database).current_id()
            except LookupError:
                return (), None, None
            return tuple(iter_entries(self.connection, snapshot_id)), None, snapshot_id
        return tuple(iter_entries(self.connection, row[0])), bytes(row[1]), row[0]

    def publish_synced(self, source_id, entries, cookie, previous_snapshot_id=None,
                       previous_entries=()):
        """Bind a full directory and the cookie in one SQLite commit.

        A single BEGIN/COMMIT writes the snapshot rows, projection, the current
        pointer and the sync cookie. Any failure rolls back; the caller keeps
        the previously published directory and cookie. The returned tuple is
        (snapshot_id, reused); reused means the directory is identical to the
        previous publication, so no new history row is created.
        """
        connection = self.connection
        if connection.in_transaction:
            raise RuntimeError("publication requires idle connection")
        ordered = tuple(sorted(entries, key=lambda entry: (entry.dn, entry.uuid)))
        candidate = Candidate(ordered, len(ordered), content_digest(ordered))
        existing = self._find_sync_row(source_id)
        # Compare against the caller's in-memory prior publication: the current
        # pointer still addresses the old snapshot until this transaction moves
        # it, so reading "current" inside the transaction would always match.
        prior_ordered = tuple(sorted(previous_entries,
                                     key=lambda entry: (entry.dn, entry.uuid)))
        reuse = (previous_snapshot_id is not None and existing is not None
                 and existing[0] == previous_snapshot_id
                 and prior_ordered == ordered)
        connection.execute("BEGIN IMMEDIATE")
        try:
            # The sequence only advances when a new snapshot is inserted, so
            # reused snapshots keep the label they were first published with
            # while the next new snapshot still gets a unique internal id.
            if reuse:
                snapshot_id, _cookie, seq = self._find_sync_row(source_id)
            else:
                next_seq = (existing[2] if existing is not None else -1) + 1
                cursor = connection.execute(
                    "INSERT INTO snapshots(source_id,input_format,completed,record_count,"
                    "digest,department_count) VALUES(?,?,?,?,?,?)",
                    (f"sync:{source_id}#{next_seq}", "records", 1, candidate.count,
                     candidate.digest, self._department_count(candidate)))
                snapshot_id = cursor.lastrowid
                seq = next_seq
                write_entries(connection, snapshot_id, candidate.entries)
                write_projection(connection, snapshot_id,
                                 self._build_projection(candidate))
            self._set_current(snapshot_id)
            self._upsert_sync(source_id, snapshot_id, cookie, seq)
            connection.commit()
            return snapshot_id, reuse
        except BaseException:
            connection.rollback()
            raise

    def bind_checkpoint(self, source_id, cookie):
        """Advance only the cookie, in one commit, for an unchanged directory.

        No new snapshot or history row is created; the current pointer and the
        full previously published directory stay in place.
        """
        connection = self.connection
        if connection.in_transaction:
            raise RuntimeError("publication requires idle connection")
        existing = self._find_sync_row(source_id)
        if existing is None:
            raise RuntimeError("checkpoint before any published directory")
        snapshot_id, _old_cookie, seq = existing
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._upsert_sync(source_id, snapshot_id, cookie, seq)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _find_sync_row(self, source_id):
        return self.connection.execute(
            "SELECT snapshot_id,cookie,publication_seq FROM sync_sources "
            "WHERE source_id=?", (source_id,)).fetchone()

    def _upsert_sync(self, source_id, snapshot_id, cookie, seq):
        self.connection.execute(
            "INSERT INTO sync_sources(source_id,snapshot_id,cookie,publication_seq) "
            "VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
            "snapshot_id=excluded.snapshot_id,cookie=excluded.cookie,"
            "publication_seq=excluded.publication_seq",
            (source_id, snapshot_id, cookie, seq))

    def _set_current(self, snapshot_id):
        self.connection.execute(
            "INSERT INTO current_snapshot(singleton,snapshot_id) VALUES(1,?) "
            "ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id",
            (snapshot_id,))

    @staticmethod
    def _department_count(candidate):
        paths = set()
        for entry in candidate.entries:
            if entry.department_path is not None:
                paths.update(entry.department_path[:n]
                             for n in range(1, len(entry.department_path) + 1))
        return len(paths)

    @staticmethod
    def _build_projection(candidate):
        from directory.membership.projection import build_projection
        return build_projection(candidate)
