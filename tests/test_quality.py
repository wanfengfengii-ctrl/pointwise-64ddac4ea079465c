import copy
from tests import MemoryCase, record, U
from directory.source.model import SourceFormatError
from directory.quality.identity import DuplicateDN, DuplicateUUID, InvalidUUID
from directory.quality.names import InvalidName, InvalidDepartmentPath, normalize_dn
from directory.quality.attributes import InvalidAttributes


class TestQuality(MemoryCase):
    def test_duplicate_identities(self):
        old = self.old()
        pairs = [
            (DuplicateDN, [record(10,"CN=x"),record(11,"cn=x")]),
            (DuplicateDN, [record(10,"CN=e\u0301"),record(11,"cn=é")]),
            (DuplicateUUID, [record(10,"cn=a"),record(10,"cn=b")])]
        special = record(10, "cn=b")
        special["attributes"]["objectUUID"] = ["{00000000000000000000000000000010}"]
        pairs.append((DuplicateUUID,[record(10,"cn=a"),special]))
        for error, rows in pairs:
            with self.subTest(error=error), self.assertRaises(error):
                self.api.import_snapshot("bad",rows)
            self.assertUnchanged(old)

    def test_invalid_fields(self):
        old = self.old()
        cases = []
        def change(field,value,error):
            row = record(10)
            row[field] = value
            cases.append((row,error))
        for dn in ("", "cn=", "cn=x+ou=y", "cn=x\\", " cn=x", "cn= x", "cn=#abc"):
            change("dn",dn,InvalidName)
        for path in ([],[" "],[None],"A"):
            change("department_path",path,InvalidDepartmentPath)
        for attributes in ({}, {"objectUUID":[]}, {"objectUUID":["bad"]},
                           {"objectUUID":[U,U]}):
            change("attributes",attributes,InvalidUUID)
        for attributes in ({"objectUUID":[U], "CN":["a"], "cn":["b"]},
                           {"objectUUID":[U],"1bad":["v"]},
                           {"objectUUID":[U],"tags":"v"},
                           {"objectUUID":[U],"tags":[1]}):
            change("attributes",attributes,InvalidAttributes)
        missing = record(10)
        del missing["dn"]
        cases.append((missing,SourceFormatError))
        extra = record(10)
        extra["unknown"] = 1
        cases.append((extra,SourceFormatError))
        for row,error in cases:
            with self.subTest(row=row), self.assertRaises(error):
                self.api.import_snapshot("bad", [row])
            self.assertUnchanged(old)

    def test_supported_name_escapes(self):
        self.assertEqual(normalize_dn(r"CN=a\,b,OU=R\\D"),r"cn=a\,b,ou=R\\D")
        row = record(10,r"CN=a\,b,OU=R\\D")
        sid = self.api.import_snapshot("escaped",[row]).snapshot_id
        self.assertEqual(self.api.get_entry(sid,r"cn=a\,b,ou=R\\D").uuid,
                         "00000000-0000-0000-0000-000000000010")
