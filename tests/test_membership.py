from tests import MemoryCase, record
from directory.quality.names import InvalidDepartmentPath


class TestMembership(MemoryCase):
    def test_structured_ancestry(self):
        sid = self.old()
        def dns(path, recursive=True):
            return [e.dn for e in self.api.members(sid, path, recursive)]
        self.assertEqual(dns(["A"]), ["cn=1,ou=People","cn=2,ou=People","cn=5,ou=People"])
        self.assertEqual(dns(["A","B"]), ["cn=1,ou=People","cn=5,ou=People"])
        self.assertEqual(dns(["A","B"],False), ["cn=1,ou=People"])
        self.assertEqual(dns(["A"],False), [])
        self.assertEqual(dns(["AB"]), ["cn=3,ou=People"])
        self.assertEqual(dns(["A","missing"]), [])
        self.assertEqual(dns(["A","B","C"]), ["cn=5,ou=People"])
        self.assertNotIn("cn=4,ou=People", dns(["A"]))
        other = self.api.import_snapshot("slash", [record(20,path=["A/B"]),
                                                   record(21,path=[" A ","B"])]).snapshot_id
        self.assertEqual([e.dn for e in self.api.members(other,["A/B"])],["cn=20,ou=People"])
        self.assertEqual([e.dn for e in self.api.members(other,["A","B"])],["cn=21,ou=People"])

    def test_invalid_path(self):
        sid = self.old()
        for path in (None, [], [""], [" "], [1], "A/B", ["A",None]):
            with self.subTest(path=path), self.assertRaises(InvalidDepartmentPath):
                self.api.members(sid, path)
        with self.assertRaises(ValueError):
            self.api.members(sid, ["A"], recursive=1)
