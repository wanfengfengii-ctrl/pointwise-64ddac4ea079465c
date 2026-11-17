"""Serial LDAP-style refresh collector (see SYNC.md).

The collector accepts the plain events from directory.source.events, merges
complete entry content by object UUID, and only exposes the new directory once
a refresh terminates. During a refresh every reader keeps seeing the previously
published V1 archive. refresh_done and continuous checkpoints publish the full
directory and the opaque resume cookie in one SQLite commit.
"""
from directory.quality.candidate import DirectoryEntry, normalize_entry
from directory.quality.identity import DuplicateDN, normalize_uuid
from directory.source.events import Event
from directory.source.sync_store import SyncStore, validate_source_id

_DISCONNECTED = "disconnected"
_REFRESH = "refresh"
_CONTINUOUS = "continuous"
_FAILED = "failed"

# kind -> fields that are allowed to be non-None on that event.
_EVENT_FIELDS = {
    "begin": ("mode",),
    "entry": ("uuid", "dn", "attributes", "department_path"),
    "present": ("uuid",),
    "delete": ("uuid",),
    "checkpoint": ("cookie",),
    "refresh_done": ("cookie",),
}
_ALL_FIELDS = ("mode", "uuid", "dn", "attributes", "cookie", "department_path")


class SyncCollector:
    def __init__(self, db, source_id):
        validate_source_id(source_id)
        self._db = db
        self._source_id = source_id
        self._store = SyncStore(db)
        self._state = _DISCONNECTED
        self._mode = None
        # UUID -> latest complete DirectoryEntry seen during this refresh.
        self._work = {}
        # UUIDs proven present in present mode (entries and present events).
        self._proven = set()
        # UUIDs deleted during this refresh; a later entry redefines them.
        self._deleted = set()
        self._published, self._cookie, self._snapshot_id = \
            self._store.load_state(source_id)

    # -- public API -------------------------------------------------------

    def accept(self, event):
        if self._state == _FAILED:
            raise ValueError("collector failed; disconnect before starting again")
        if not isinstance(event, Event):
            self._fail(ValueError("event must be an Event instance"))
        kind = event.kind
        if kind not in _EVENT_FIELDS:
            self._fail(ValueError("unknown event kind: " + repr(kind)))
        # Construction and reopen leave the collector unconnected; the begin
        # event is the only way to open a connection.
        if self._state == _DISCONNECTED:
            if kind != "begin":
                self._fail(ValueError("connection is not open; call begin first"))
        allowed = _EVENT_FIELDS[kind]
        if any(getattr(event, field) is not None
               for field in _ALL_FIELDS if field not in allowed):
            self._fail(ValueError("event carries unrelated fields: " + kind))
        try:
            if kind == "begin":
                self._begin(event)
            elif kind == "entry":
                self._entry(event)
            elif kind == "present":
                self._present(event)
            elif kind == "delete":
                self._delete(event)
            elif kind == "checkpoint":
                self._checkpoint(event)
            else:  # refresh_done
                self._refresh_done(event)
        except ValueError:
            self._fail()
            raise
        except Exception:
            # Storage errors keep their sqlite3 type; state is still poisoned.
            self._fail()
            raise

    def read(self):
        """Published directory sorted by canonical DN then UUID."""
        return tuple(sorted(self._published, key=lambda e: (e.dn, e.uuid)))

    def resume_cookie(self):
        return self._cookie

    def disconnect(self):
        # Idempotent: always returns to a clean, unconnected state. An
        # unfinished refresh is discarded entirely; published data survives.
        self._state = _DISCONNECTED
        self._mode = None
        self._work = {}
        self._proven = set()
        self._deleted = set()

    # -- event handlers ---------------------------------------------------

    def _begin(self, event):
        if self._state != _DISCONNECTED:
            raise ValueError("begin is only allowed after connect or disconnect")
        if event.mode not in ("present", "delete"):
            raise ValueError("begin mode must be 'present' or 'delete'")
        self._mode = event.mode
        self._work = {entry.uuid: entry for entry in self._published}
        self._proven = set()
        self._deleted = set()
        self._state = _REFRESH

    def _entry(self, event):
        entry = self._normalize_event_entry(event)
        if self._state == _REFRESH:
            self._work[entry.uuid] = entry
            self._proven.add(entry.uuid)
            self._deleted.discard(entry.uuid)
            return
        # Continuous phase: every accepted mutation is durable on return.
        entries = {old.uuid: old for old in self._published}
        entries[entry.uuid] = entry
        self._publish_continuous(tuple(entries.values()))

    def _present(self, event):
        if self._state != _REFRESH or self._mode != "present":
            raise ValueError("present is only allowed in a present-mode refresh")
        object_uuid = self._normalize_event_uuid(event.uuid)
        if object_uuid not in self._work or object_uuid in self._deleted:
            raise ValueError("present for unknown or deleted uuid: " + object_uuid)
        self._proven.add(object_uuid)

    def _delete(self, event):
        object_uuid = self._normalize_event_uuid(event.uuid)
        if self._state == _REFRESH:
            self._work.pop(object_uuid, None)
            self._proven.discard(object_uuid)
            self._deleted.add(object_uuid)
            return
        # Continuous phase: unknown deletes are legal and publish nothing new.
        if object_uuid not in {entry.uuid for entry in self._published}:
            return
        entries = tuple(entry for entry in self._published
                        if entry.uuid != object_uuid)
        self._publish_continuous(entries)

    def _checkpoint(self, event):
        if not isinstance(event.cookie, bytes):
            raise ValueError("checkpoint cookie must be bytes")
        if self._state == _REFRESH:
            # Refresh work sets are never published mid-refresh; the cookie
            # only takes effect at refresh_done.
            return
        if self._cookie == event.cookie:
            return
        self._store.bind_checkpoint(self._source_id, event.cookie)
        self._cookie = event.cookie

    def _refresh_done(self, event):
        if self._state != _REFRESH:
            raise ValueError("refresh_done is only allowed inside a refresh")
        if not isinstance(event.cookie, bytes):
            raise ValueError("refresh_done cookie must be bytes")
        if self._mode == "present":
            entries = tuple(entry for uuid, entry in self._work.items()
                            if uuid in self._proven and uuid not in self._deleted)
        else:
            entries = tuple(self._work.values())
        ordered = tuple(sorted(entries, key=lambda e: (e.dn, e.uuid)))
        seen_dn = set()
        for entry in ordered:
            if entry.dn in seen_dn:
                raise DuplicateDN("final directory has duplicate DN: " + entry.dn)
            seen_dn.add(entry.dn)
        # One SQLite commit binds the full directory and the cookie together.
        snapshot_id, _reused = self._store.publish_synced(
            self._source_id, ordered, event.cookie, self._snapshot_id,
            self._published)
        self._snapshot_id = snapshot_id
        self._published = tuple(iter_snapshot_entries(self._store, snapshot_id))
        self._cookie = event.cookie
        self._work = {}
        self._proven = set()
        self._deleted = set()
        self._mode = None
        self._state = _CONTINUOUS

    # -- helpers ----------------------------------------------------------

    def _publish_continuous(self, entries):
        ordered = tuple(sorted(entries, key=lambda e: (e.dn, e.uuid)))
        seen_dn = set()
        for entry in ordered:
            if entry.dn in seen_dn:
                raise DuplicateDN("directory would have duplicate DN: " + entry.dn)
            seen_dn.add(entry.dn)
        if tuple(sorted(self._published, key=lambda e: (e.dn, e.uuid))) == ordered:
            return
        snapshot_id, _reused = self._store.publish_synced(
            self._source_id, ordered, self._cookie, self._snapshot_id,
            self._published)
        self._snapshot_id = snapshot_id
        self._published = tuple(iter_snapshot_entries(self._store, snapshot_id))

    @staticmethod
    def _normalize_event_uuid(value):
        if not isinstance(value, str):
            raise ValueError("uuid must be a string")
        return normalize_uuid([value])

    def _normalize_event_entry(self, event):
        if not isinstance(event.uuid, str):
            raise ValueError("entry uuid must be a string")
        if not isinstance(event.dn, str):
            raise ValueError("entry dn must be a string")
        record = {"dn": event.dn, "attributes": event.attributes}
        # department_path is the one entry field allowed to be None (unassigned).
        if event.department_path is not None:
            record["department_path"] = event.department_path
        entry = normalize_entry(record)
        if entry.uuid != self._normalize_event_uuid(event.uuid):
            raise ValueError("entry objectUUID does not match event uuid")
        return DirectoryEntry(entry.dn, entry.uuid, entry.attributes,
                              entry.department_path)

    def _fail(self, error=None):
        self._state = _FAILED
        self._mode = None
        self._work = {}
        self._proven = set()
        self._deleted = set()
        if error is not None:
            raise error


def iter_snapshot_entries(store, snapshot_id):
    from directory.archive.entries import iter_entries
    return tuple(iter_entries(store.connection, snapshot_id))
