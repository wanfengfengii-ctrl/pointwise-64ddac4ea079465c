"""Single verify entry: original regression discovery plus SYNC joint checks.

Business failures exit non-zero so the runtime can gate on this command.
"""
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from directory.archive.schema import ArchiveDatabase
from directory.source import events as ev
from directory.source.events import (begin, entry, present, delete, checkpoint,
                                      refresh_done, sample_scripts, seed_records,
                                      reject_commit, ScriptedSource)
from directory.source.sync import SyncCollector
from directory.workflow.api import DirectoryAPI

ROOT = Path(__file__).resolve().parents[1]


def run_original_regression():
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    if not result.wasSuccessful():
        sys.stderr.write(stream.getvalue())
        raise SystemExit("original unittest discovery failed")
    return result.testsRun


def _seeded(path, source_id="ldap-joint"):
    api = DirectoryAPI(path)
    api.import_snapshot("seed", seed_records())
    return api, SyncCollector(api.runtime.database, source_id)


def _drive(collector, script):
    for event in ScriptedSource(script).open(collector.resume_cookie()):
        collector.accept(event)


def joint_verification():
    """Cross-module checks against the hand-computed SYNC.md oracle."""
    report = {}
    with tempfile.TemporaryDirectory() as folder:
        # 1. Presence sample: attributes and department survive present-only.
        api, collector = _seeded(Path(folder) / "p.db")
        _drive(collector, sample_scripts()["presence"])
        entries = collector.read()
        assert collector.resume_cookie() == b"present-done"
        assert [e.uuid for e in entries] == [ev.U]
        assert dict(entries[0].attributes)["x"] == ("1",)
        assert entries[0].department_path == ("A",)
        current = api.bind_snapshot()
        assert [e.dn for e in api.members(current, ["A"])] == ["cn=a"]
        assert api.source_info(current).source_id.startswith("sync:")
        report["presence"] = {"cookie": collector.resume_cookie().decode(),
                              "uuids": [e.uuid for e in entries]}
        api.close()

        # 2. Delete mode keeps unmentioned objects and their departments.
        api, collector = _seeded(Path(folder) / "d.db")
        _drive(collector, sample_scripts()["delete-mode"])
        entries = collector.read()
        assert collector.resume_cookie() == b"delete-done"
        v = next(e for e in entries if e.uuid == ev.V)
        assert v.department_path == ("B",) and dict(v.attributes)["y"] == ("3",)
        report["delete_mode"] = {"cookie": "delete-done",
                                 "kept": [e.uuid for e in entries]}
        api.close()

        # 3. Name swap: old directory visible mid-swap, identities stay by UUID.
        api, collector = _seeded(Path(folder) / "s.db")
        collector.accept(begin("present"))
        collector.accept(entry(ev.U, "CN=b",
                               {"objectUUID": [ev.U], "x": ["2"]}, ["B"]))
        assert [e.dn for e in collector.read()] == ["cn=a", "cn=b"]
        _drive(collector, sample_scripts()["swap"][2:])
        entries = {e.uuid: e for e in collector.read()}
        assert entries[ev.U].dn == "cn=b" and entries[ev.V].dn == "cn=a"
        assert collector.resume_cookie() == b"swap-done"
        report["swap"] = {"u_dn": entries[ev.U].dn, "v_dn": entries[ev.V].dn}
        api.close()

        # 4. Disconnect and reopen discard unfinished refresh, keep cookie.
        path = Path(folder) / "i.db"
        api, collector = _seeded(path)
        collector.accept(begin("present"))
        collector.accept(checkpoint(b"uncommitted"))
        collector.disconnect()
        api.close()
        db = ArchiveDatabase(path)
        reopened = SyncCollector(db, "ldap-joint")
        assert reopened.resume_cookie() is None
        assert [e.dn for e in reopened.read()] == ["cn=a", "cn=b"]
        reopened.accept(begin("present"))
        reopened.accept(present(ev.U))
        reopened.accept(present(ev.V))
        reopened.accept(refresh_done(b"reopened"))
        db.close()
        db = ArchiveDatabase(path)
        again = SyncCollector(db, "ldap-joint")
        assert again.resume_cookie() == b"reopened"
        assert len(again.read()) == 2
        report["disconnect_reopen"] = {"cookie": again.resume_cookie().decode()}
        db.close()

        # 5. Commit failure at refresh_done and checkpoint preserves everything.
        path = Path(folder) / "f.db"
        api, collector = _seeded(path)
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"safe"))
        history = api.history()
        current = api.bind_snapshot()
        connection = api.runtime.database.connection
        with reject_commit(connection):
            try:
                collector.accept(checkpoint(b"denied-cp"))
            except sqlite3.DatabaseError:
                pass
        assert not connection.in_transaction
        assert collector.resume_cookie() == b"safe"
        assert api.history() == history and api.bind_snapshot() == current
        collector.disconnect()
        with reject_commit(connection):
            try:
                collector.accept(begin("present"))
                collector.accept(delete(ev.V))
                collector.accept(refresh_done(b"denied-done"))
            except sqlite3.DatabaseError:
                pass
        assert not connection.in_transaction
        assert collector.resume_cookie() == b"safe"
        assert [e.dn for e in collector.read()] == ["cn=a", "cn=b"]
        collector.disconnect()
        api.close()
        db = ArchiveDatabase(path)
        durable = SyncCollector(db, "ldap-joint")
        assert durable.resume_cookie() == b"safe"
        assert [e.dn for e in durable.read()] == ["cn=a", "cn=b"]
        report["commit_failure"] = {"cookie": durable.resume_cookie().decode(),
                                    "snapshots": len(history)}
        db.close()

        # 6. Resume cookie is the argument of the next source open.
        path = Path(folder) / "r.db"
        api, collector = _seeded(path)
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"cursor-7"))
        collector.disconnect()
        port = ScriptedSource([begin("delete"), refresh_done(b"cursor-8")])
        for event in port.open(collector.resume_cookie()):
            collector.accept(event)
        assert port.opened_cookies == [b"cursor-7"]
        assert collector.resume_cookie() == b"cursor-8"
        report["resume"] = {"opened_with": port.opened_cookies[0].decode()}
        api.close()

    return report


def main():
    tests_run = run_original_regression()
    report = joint_verification()
    print("POINTWISE_VERIFY " + json.dumps(
        {"unittest_discover": tests_run, "joint_checks": report,
         "invariant": "refresh_done and checkpoint commit full directory and "
                      "opaque cookie in one SQLite transaction; failed commits "
                      "roll back to the previous directory and cookie"},
        ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
