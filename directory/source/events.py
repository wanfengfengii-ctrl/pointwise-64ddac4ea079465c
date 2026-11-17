from dataclasses import dataclass
from contextlib import contextmanager
import sqlite3


@dataclass(frozen=True)
class Event:
    kind: str
    mode: object = None
    uuid: object = None
    dn: object = None
    attributes: object = None
    cookie: object = None
    department_path: object = None


def begin(mode):
    return Event("begin", mode=mode)


def entry(uuid, dn, attributes, department_path=None):
    return Event("entry", uuid=uuid, dn=dn, attributes=attributes,
                 department_path=department_path)


def present(uuid):
    return Event("present", uuid=uuid)


def delete(uuid):
    return Event("delete", uuid=uuid)


def checkpoint(cookie):
    return Event("checkpoint", cookie=cookie)


def refresh_done(cookie):
    return Event("refresh_done", cookie=cookie)


class ScriptedSource:
    def __init__(self, events, *, failure_at=None, error=None, stop_at=None):
        self.events = tuple(events)
        self.failure_at = failure_at
        self.error = error if error is not None else OSError("scripted source failure")
        self.stop_at = stop_at
        self.opened_cookies = []

    def open(self, cookie):
        self.opened_cookies.append(cookie)
        for index in range(len(self.events) + 1):
            if index == self.failure_at:
                raise self.error
            if index == self.stop_at or index == len(self.events):
                return
            yield self.events[index]


U = "00000000-0000-0000-0000-000000000001"
V = "00000000-0000-0000-0000-000000000002"


def seed_records():
    """Fresh raw V1 records; not a sync implementation or reference reducer."""
    return [
        {"dn": "CN=a", "attributes": {"objectUUID": [U], "x": ["1"]},
         "department_path": ["A"]},
        {"dn": "CN=b", "attributes": {"objectUUID": [V], "y": ["3"]},
         "department_path": ["B"]},
    ]


def sample_scripts():
    """Hand-written inputs. Expected business results are in SYNC.md."""
    return {
        "presence": [begin("present"), present(U), checkpoint(b"ignored"),
                     refresh_done(b"present-done")],
        "delete-mode": [begin("delete"), entry(U, "CN=a",
                        {"objectUUID": [U], "x": ["2"]}, ["A"]),
                        refresh_done(b"delete-done")],
        "swap": [begin("present"),
                 entry(U, "CN=b", {"objectUUID": [U], "x": ["2"]}, ["B"]),
                 entry(V, "CN=a", {"objectUUID": [V], "y": ["4"]}, ["A"]),
                 refresh_done(b"swap-done")],
        "incomplete": [begin("present"), present(U), checkpoint(b"uncommitted")],
        "bad-final-name": [begin("delete"),
                           entry(U, "CN=b", {"objectUUID": [U]}, ["A"]),
                           refresh_done(b"must-not-commit")],
    }


@dataclass
class CommitGate:
    ordinal: int
    attempts: int = 0
    denied: int = 0


@contextmanager
def reject_commit(connection, ordinal=1):
    """Deny one real COMMIT, leaving ROLLBACK available.

    Use on an idle caller-owned connection. The connection must not have
    another authorizer installed; this fixture removes its own on exit.
    Catch the sqlite3 exception outside this context, then check durable
    state. This helper does not roll back on behalf of production code.
    """
    if type(ordinal) is not int or ordinal < 1:
        raise ValueError("ordinal must be a positive integer")
    if connection.in_transaction:
        raise ValueError("fixture requires an idle connection")
    gate = CommitGate(ordinal)

    def authorize(action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_TRANSACTION and arg1 == "COMMIT":
            gate.attempts += 1
            if gate.attempts == gate.ordinal:
                gate.denied += 1
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    try:
        yield gate
    finally:
        connection.set_authorizer(None)


def fixture_self_check():
    """Exercise only supplied V1 and the failure fixture, without disk I/O."""
    from directory.workflow.api import DirectoryAPI
    with DirectoryAPI(":memory:") as api:
        old = api.import_snapshot("fixture-old", seed_records()).snapshot_id
        connection = api.runtime.database.connection
        history = api.history()
        changed = seed_records()
        changed[0]["attributes"]["x"] = ["2"]
        try:
            with reject_commit(connection) as gate:
                api.import_snapshot("fixture-denied", changed)
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("COMMIT was not denied")
        assert gate.attempts == gate.denied == 1
        assert not connection.in_transaction
        assert api.bind_snapshot() == old
        assert api.history() == history
        assert api.runtime.store.find_source("fixture-denied") is None
        assert dict(api.get_entry(old, "CN=a").attributes)["x"] == ("1",)
        assert [e.uuid for e in api.members(old, ["A"])] == [U]
        port = ScriptedSource(sample_scripts()["incomplete"])
        assert list(port.open(b"saved")) == sample_scripts()["incomplete"]
        assert port.opened_cookies == [b"saved"]
        latest = api.import_snapshot("fixture-retry", changed).snapshot_id
        assert dict(api.get_entry(latest, "CN=a").attributes)["x"] == ("2",)
        return {"fixture": "V1 rollback and scripted inputs",
                "expected_old_x": ["1"],
                "actual_old_x": list(dict(api.get_entry(old, "CN=a").attributes)["x"]),
                "denied_commits": gate.denied,
                "sample_names": sorted(sample_scripts())}


if __name__ == "__main__":
    import json
    import sys
    if sys.argv[1:] != ["--self-check"]:
        raise SystemExit("usage: python3.11 -B -m directory.source.events --self-check")
    print("POINTWISE_FIXTURE " + json.dumps(fixture_self_check(), sort_keys=True))
