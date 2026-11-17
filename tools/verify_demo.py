import hashlib
import io
import json
import tempfile
from pathlib import Path
from directory.workflow.api import DirectoryAPI
from directory.archive.schema import ArchiveDatabase
from directory.source import events as ev
from directory.source.events import (ScriptedSource, begin, entry, present,
                                      refresh_done, checkpoint,
                                      sample_scripts, seed_records)
from directory.source.sync import SyncCollector


def _drive(collector, script):
    for event in ScriptedSource(script).open(collector.resume_cookie()):
        collector.accept(event)


def sync_demo(folder):
    """Real LDAP-style refresh scenarios on a fresh seeded SQLite library."""
    U, V = ev.U, ev.V

    # Scenario 1: present refresh proves existence; full attributes and the
    # department assignment survive even though only UUIDs were reasserted.
    path = Path(folder) / "sync-presence.db"
    with DirectoryAPI(path) as api:
        api.import_snapshot("seed", seed_records())
        collector = SyncCollector(api.runtime.database, "lab-ldap")
        _drive(collector, sample_scripts()["presence"])
        kept = collector.read()
        assert dict(kept[0].attributes)["x"] == ("1",)
        assert kept[0].department_path == ("A",)
        bound = api.bind_snapshot()
        downstream = [e.dn for e in api.members(bound, ["A"])]
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "sync-presence",
            "input": {"events": [e.kind for e in sample_scripts()["presence"]]},
            "expected": {"survivors": [U], "x": ["1"], "department": ["A"],
                         "cookie": "present-done"},
            "actual": {"survivors": [e.uuid for e in kept],
                       "x": list(dict(kept[0].attributes)["x"]),
                       "department": list(kept[0].department_path),
                       "cookie": collector.resume_cookie().decode()},
            "downstream_expected": ["cn=a"], "downstream_actual": downstream,
            "code_paths": ["directory/source/sync.py",
                           "directory/source/sync_store.py",
                           "directory/delivery/filter.py"],
            "oracle": "present only proves UUID; complete seed content is merged by UUID"},
            ensure_ascii=False))

    # Scenario 2: a DN swap between two UUIDs. Mid-swap readers keep the old
    # directory; only refresh_done publishes the exchanged names together.
    path = Path(folder) / "sync-swap.db"
    with DirectoryAPI(path) as api:
        api.import_snapshot("seed", seed_records())
        collector = SyncCollector(api.runtime.database, "lab-ldap")
        collector.accept(begin("present"))
        collector.accept(entry(U, "CN=b",
                               {"objectUUID": [U], "x": ["2"]}, ["B"]))
        mid_swap = [e.dn for e in collector.read()]
        _drive(collector, sample_scripts()["swap"][2:])
        swapped = {e.uuid: e for e in collector.read()}
        assert mid_swap == ["cn=a", "cn=b"]
        assert (swapped[U].dn, swapped[V].dn) == ("cn=b", "cn=a")
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "sync-name-swap",
            "input": {"events": [e.kind for e in sample_scripts()["swap"]]},
            "expected": {"mid_swap_names": ["cn=a", "cn=b"],
                         "final": {U: "cn=b", V: "cn=a"},
                         "cookie": "swap-done"},
            "actual": {"mid_swap_names": mid_swap,
                       "final": {U: swapped[U].dn, V: swapped[V].dn},
                       "cookie": collector.resume_cookie().decode()},
            "downstream_expected": {"A": ["cn=a"], "B": ["cn=b"]},
            "downstream_actual": {
                "A": [e.dn for e in api.members(api.bind_snapshot(), ["A"])],
                "B": [e.dn for e in api.members(api.bind_snapshot(), ["B"])]},
            "code_paths": ["directory/source/sync.py",
                           "directory/membership/projection.py",
                           "directory/archive/snapshots.py"],
            "oracle": "UUID is identity, DN is a mutable name; uniqueness checked at done"},
            ensure_ascii=False))

    # Scenario 3: the source stops mid-refresh; disconnect discards the work.
    # Reopening resumes from the last committed cookie and finishes the run.
    path = Path(folder) / "sync-resume.db"
    with DirectoryAPI(path) as api:
        api.import_snapshot("seed", seed_records())
        collector = SyncCollector(api.runtime.database, "lab-ldap")
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(present(V))
        collector.accept(refresh_done(b"cursor-1"))
        collector.accept(checkpoint(b"cursor-2"))
        committed_cookie = collector.resume_cookie()
        # A new connection starts, then the source stops before refresh_done.
        collector.disconnect()
        port = ScriptedSource([begin("present"), present(U),
                               checkpoint(b"never-committed")], stop_at=3)
        for event in port.open(collector.resume_cookie()):
            collector.accept(event)
        collector.disconnect()
    db = ArchiveDatabase(path)
    resumed = SyncCollector(db, "lab-ldap")
    assert resumed.resume_cookie() == committed_cookie
    assert [e.dn for e in resumed.read()] == ["cn=a", "cn=b"]
    replay = ScriptedSource([begin("present"), present(U), present(V),
                             refresh_done(b"cursor-3")])
    opened_with = None
    for event in replay.open(resumed.resume_cookie()):
        opened_with = replay.opened_cookies[-1]
        resumed.accept(event)
    assert resumed.resume_cookie() == b"cursor-3"
    print("POINTWISE_SCENARIO " + json.dumps({
        "case_id": "sync-disconnect-resume",
        "input": {"script": ["begin", "present", "checkpoint(stop)"],
                  "replay": ["begin", "present", "present", "refresh_done"]},
        "expected": {"cookie_after_stop": "cursor-2",
                     "reopened_with": "cursor-2",
                     "cookie_after_replay": "cursor-3",
                     "directory": ["cn=a", "cn=b"]},
        "actual": {"cookie_after_stop": committed_cookie.decode(),
                   "reopened_with": opened_with.decode(),
                   "cookie_after_replay": resumed.resume_cookie().decode(),
                   "directory": [e.dn for e in resumed.read()]},
        "downstream_expected": ["cn=a", "cn=b"],
        "downstream_actual": [e.dn for e in resumed.read()],
        "code_paths": ["directory/source/sync.py",
                       "directory/source/sync_store.py",
                       "directory/archive/schema.py"],
        "oracle": "unfinished refresh is dropped; reopen uses committed directory and cookie"},
        ensure_ascii=False))
    db.close()


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="directory-demo-") as folder:
        database = Path(folder) / "catalog.db"
        with DirectoryAPI(database) as api:
            with (root / "examples/complete.jsonl").open("rb") as stream:
                first = api.import_jsonl("daily-1", stream)
            bound = api.bind_snapshot()
            expected_members = ["cn=1,ou=People","cn=2,ou=People","cn=5,ou=People"]
            actual_members = [entry.dn for entry in api.members(bound,["A"])]
            assert actual_members == expected_members
            assert (first.record_count, first.department_count) == (5,5)
            before = io.BytesIO()
            exported = api.export_snapshot(bound,before)
            assert exported.digest == hashlib.sha256(before.getvalue()).hexdigest()
            assert exported.digest == api.source_info(bound).digest
            assert dict(api.get_entry(bound,"CN=1,OU=People").attributes)["tags"] == (" a ","z","é")
            with (root / "examples/revised.jsonl").open("rb") as stream:
                second = api.import_jsonl("daily-2",stream)
            after = io.BytesIO()
            assert api.export_snapshot(bound,after) == exported
            assert after.getvalue() == before.getvalue()
            expected_diff = [
                ("modified","cn=1,ou=People"),("removed","cn=2,ou=People"),
                ("removed","cn=4,ou=People"),("added","cn=6,ou=People"),
                ("added","cn=renamed,ou=People")]
            differences = api.diff_snapshots(bound,second.snapshot_id)
            actual_diff = [(item.kind,item.dn) for item in differences]
            assert actual_diff == expected_diff
            history = api.history()
        with DirectoryAPI(database) as api:
            assert api.bind_snapshot() == second.snapshot_id
            assert api.history() == history
            assert api.diff_snapshots(bound,second.snapshot_id) == differences
            again = io.BytesIO()
            assert api.export_snapshot(bound,again) == exported
            assert again.getvalue() == before.getvalue()
            print("POINTWISE_SCENARIO " + json.dumps({
                "case_id":"v8", "input":{"sources":["daily-1","daily-2"],
                                        "old_snapshot":bound,"new_snapshot":second.snapshot_id},
                "expected":expected_diff, "actual":actual_diff,
                "downstream_expected":expected_members,
                "downstream_actual":[e.dn for e in api.members(bound,["A"])],
                "code_paths":["directory/workflow/api.py","directory/source/jsonlines.py",
                              "directory/quality/candidate.py","directory/membership/compute.py",
                              "directory/archive/snapshots.py","directory/delivery/export.py",
                              "directory/delivery/diff.py"],
                "oracle":"hand-enumerated DN changes and department memberships; reopen and exact bytes"},
                ensure_ascii=False))
            print(json.dumps({"snapshots":[i.snapshot_id for i in history],
                              "old_export_records":exported.record_count,
                              "old_export_sha256":exported.digest,
                              "current_source":api.source_info(api.bind_snapshot()).source_id},
                             ensure_ascii=False,sort_keys=True))
        sync_demo(folder)


if __name__ == "__main__":
    main()
