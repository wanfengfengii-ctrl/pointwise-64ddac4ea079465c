import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from tests import sample, revised, record
from directory.workflow.api import DirectoryAPI
from directory.archive.schema import initialize_schema, UnsupportedSchema, StoreClosed
from directory.archive.snapshots import SourceConflict, NoCurrentSnapshot


class TestArchive(unittest.TestCase):
    def test_publish_failure_reopen(self):
        for target in ("projection", "pointer"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "archive.db"
                with DirectoryAPI(path) as api:
                    old = api.import_snapshot("old", sample()).snapshot_id
                    history = api.history()
                    old_members = api.members(old, ["A"])
                    connection = api.runtime.database.connection
                    if target == "projection":
                        connection.execute(
                            "CREATE TEMP TRIGGER fail BEFORE INSERT ON members "
                            "BEGIN SELECT RAISE(ABORT,'projection write stopped'); END")
                    else:
                        connection.execute(
                            "CREATE TEMP TRIGGER fail BEFORE UPDATE ON current_snapshot "
                            "BEGIN SELECT RAISE(ABORT,'pointer write stopped'); END")
                    with self.assertRaises(sqlite3.IntegrityError):
                        api.import_snapshot("retry", revised())
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(api.bind_snapshot(), old)
                    self.assertEqual(api.history(), history)
                    self.assertIsNone(api.runtime.store.find_source("retry"))
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                with DirectoryAPI(path) as api:
                    self.assertEqual(api.bind_snapshot(), old)
                    self.assertEqual(api.history(), history)
                    self.assertEqual(api.members(old, ["A"]), old_members)
                    self.assertEqual(api.get_entry(old,"cn=1,ou=People").uuid,
                                     "00000000-0000-0000-0000-000000000001")
                    self.assertIsNone(api.runtime.store.find_source("retry"))
                    for table, expected in (("snapshots",1),("sources",1),("entries",5),
                                            ("departments",5),("members",12)):
                        self.assertEqual(api.runtime.database.connection.execute(
                            "SELECT count(*) FROM " + table).fetchone()[0], expected)
                    self.assertNotEqual(api.import_snapshot("retry",revised()).snapshot_id, old)
                    self.assertEqual(len(api.history()),2)

    def test_source_reimport(self):
        with DirectoryAPI(":memory:") as api:
            original = sample()
            first = api.import_snapshot("Day", original)
            info = api.source_info(first.snapshot_id)
            equivalent = []
            for row in reversed(original):
                row = copy.deepcopy(row)
                row["attributes"] = {k:list(reversed(v))
                                     for k,v in reversed(list(row["attributes"].items()))}
                equivalent.append(row)
            self.assertEqual(api.import_snapshot("Day",iter(equivalent)), first)
            self.assertEqual(api.source_info(first.snapshot_id),info)
            for field in ("value", "department"):
                altered = copy.deepcopy(original)
                if field == "value":
                    altered[0]["attributes"]["Tags"][-1] = "new second value"
                else:
                    altered[0]["department_path"] = ["Different"]
                with self.assertRaises(SourceConflict):
                    api.import_snapshot("Day",altered)
            newer = api.import_snapshot("newer",revised())
            self.assertEqual(api.import_snapshot("Day",original), first)
            self.assertEqual(api.bind_snapshot(),newer.snapshot_id)
            separate = api.import_snapshot("day",original)
            self.assertNotEqual(separate.snapshot_id,first.snapshot_id)
            self.assertEqual(len(api.history()),3)

    def test_initialization_and_close(self):
        api = DirectoryAPI(":memory:")
        connection = api.runtime.database.connection
        with self.assertRaises(NoCurrentSnapshot):
            api.bind_snapshot()
        sid = api.import_snapshot("user", [record(91,path=["User"])]).snapshot_id
        initialize_schema(connection)
        self.assertEqual(api.bind_snapshot(),sid)
        self.assertEqual(api.source_info(sid).source_id,"user")
        connection.execute("PRAGMA user_version=7")
        with self.assertRaises(UnsupportedSchema):
            initialize_schema(connection)
        self.assertEqual(connection.execute("SELECT count(*) FROM entries").fetchone()[0],1)
        api.close()
        api.close()
        with self.assertRaises(StoreClosed):
            api.history()
        with self.assertRaises(StoreClosed):
            api.import_snapshot("closed",[])
