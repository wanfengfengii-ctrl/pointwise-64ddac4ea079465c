import hashlib
import io
import json
import tempfile
from pathlib import Path
from directory.workflow.api import DirectoryAPI


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


if __name__ == "__main__":
    main()
