import copy
import unittest
from directory.workflow.api import DirectoryAPI

U = "00000000-0000-0000-0000-000000000001"
V = "00000000-0000-0000-0000-000000000002"


def record(number, dn=None, path=None):
    return {"dn": dn or f"CN={number},OU=People",
            "attributes": {"objectUUID": [f"00000000-0000-0000-0000-{number:012d}"],
                           "cn": [f"Person {number}"]},
            "department_path": path}


def sample():
    rows = [record(1, path=["A", "B"]), record(2, path=["A", "BC"]),
            record(3, path=["AB"]), record(4), record(5, path=["A", "B", "C"])]
    rows[0]["attributes"].update({"Tags": ["z", "e\u0301", "é", " a "],
                                 "custom-field": [], "Description": ["青岚"]})
    return rows


def revised():
    rows = copy.deepcopy(sample())
    rows = [row for row in rows if row["dn"] != "CN=2,OU=People"]
    rows[0]["attributes"]["cn"] = ["Changed"]
    rows[2]["dn"] = "CN=renamed,OU=People"
    rows.append(record(6, path=["A"]))
    return rows


class MemoryCase(unittest.TestCase):
    def setUp(self):
        self.api = DirectoryAPI(":memory:")
        self.addCleanup(self.api.close)

    def old(self):
        return self.api.import_snapshot("old", sample()).snapshot_id

    def assertUnchanged(self, snapshot_id):
        self.assertEqual(self.api.bind_snapshot(), snapshot_id)
        self.assertEqual(len(self.api.history()), 1)
        self.assertEqual(len(self.api.members(snapshot_id, ["A"])), 3)
