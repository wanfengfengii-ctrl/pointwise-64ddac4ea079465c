import copy
import tempfile
import unittest
from pathlib import Path
from tests import sample, revised, record
from directory.workflow.api import DirectoryAPI
from directory.archive.schema import initialize_schema
from directory.archive.snapshots import UnknownSnapshot


class TestReview(unittest.TestCase):
    def test_diff_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "review.db"
            with DirectoryAPI(path) as api:
                first = api.import_snapshot("user-before",sample()).snapshot_id
                second = api.import_snapshot("user-after",revised()).snapshot_id
                expected = [("modified","cn=1,ou=People"),("removed","cn=2,ou=People"),
                            ("removed","cn=4,ou=People"),("added","cn=6,ou=People"),
                            ("added","cn=renamed,ou=People")]
                diffs = api.diff_snapshots(first,second)
                self.assertEqual([(d.kind,d.dn) for d in diffs],expected)
                self.assertEqual(diffs[0].before.uuid,diffs[0].after.uuid)
                self.assertEqual(dict(diffs[0].after.attributes)["cn"],("Changed",))
                self.assertEqual(diffs[2].before.uuid,diffs[4].after.uuid)
                self.assertEqual(api.diff_snapshots(first,first),[])
                same = api.import_snapshot("equal",sample()).snapshot_id
                self.assertEqual(api.diff_snapshots(first,same),[])
                for left,right in ((first,999),(999,second)):
                    with self.assertRaises(UnknownSnapshot):
                        api.diff_snapshots(left,right)
                history = api.history()
                sources = [api.source_info(i) for i in (first,second,same)]
                members = api.members(second,["A"])
                initialize_schema(api.runtime.database.connection)
            for _ in range(2):
                with DirectoryAPI(path) as api:
                    self.assertEqual(api.bind_snapshot(),same)
                    self.assertEqual(api.history(),history)
                    self.assertEqual([api.source_info(i) for i in (first,second,same)],sources)
                    self.assertEqual(api.diff_snapshots(first,second),diffs)
                    self.assertEqual(api.members(second,["A"]),members)

    def test_full_row_changes(self):
        with DirectoryAPI(":memory:") as api:
            base = [record(10,"cn=x",["A"])]
            first = api.import_snapshot("base",base).snapshot_id
            changes = []
            empty = copy.deepcopy(base)
            empty[0]["attributes"]["optional"] = []
            changes.append(empty)
            values = copy.deepcopy(base)
            values[0]["attributes"]["cn"].append("second")
            changes.append(values)
            department = copy.deepcopy(base)
            department[0]["department_path"] = ["B"]
            changes.append(department)
            identity = copy.deepcopy(base)
            identity[0]["attributes"]["objectUUID"] = ["00000000-0000-0000-0000-000000000011"]
            changes.append(identity)
            for number,rows in enumerate(changes):
                sid = api.import_snapshot(f"change-{number}",rows).snapshot_id
                result = api.diff_snapshots(first,sid)
                self.assertEqual(len(result),1)
                self.assertEqual((result[0].kind,result[0].dn),("modified","cn=x"))
                self.assertNotEqual(result[0].before,result[0].after)
