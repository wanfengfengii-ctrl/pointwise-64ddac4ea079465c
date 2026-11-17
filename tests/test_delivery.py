import hashlib
import io
import json
from tests import MemoryCase, sample, revised, record
from directory.archive.snapshots import UnknownSnapshot
from directory.quality.names import InvalidName


class ShortSink:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        count = min(3,len(data))
        self.data.extend(data[:count])
        return count


class TestDelivery(MemoryCase):
    def test_fixed_export(self):
        old = self.old()
        bound = self.api.bind_snapshot()
        output = io.BytesIO()
        before = self.api.export_snapshot(bound,output)
        entry = self.api.get_entry(bound,"CN=1,OU=People")
        members = self.api.members(bound,["A"])
        self.api.import_snapshot("next",revised())
        self.assertEqual(bound,old)
        self.assertEqual(self.api.get_entry(bound,"CN=1,OU=People"),entry)
        self.assertEqual(self.api.members(bound,["A"]),members)
        short = ShortSink()
        after = self.api.export_snapshot(bound,short)
        self.assertEqual(bytes(short.data),output.getvalue())
        self.assertEqual(after,before)
        sentinel = OSError("delivery failed")
        class BrokenSink:
            calls = 0
            def write(self,data):
                self.calls += 1
                if self.calls > 1:
                    raise sentinel
                return min(2,len(data))
        with self.assertRaises(OSError) as caught:
            self.api.export_snapshot(bound,BrokenSink())
        self.assertIs(caught.exception,sentinel)
        self.assertEqual(self.api.get_entry(bound,entry.dn),entry)
        class StalledSink:
            def write(self,data):
                return 0
        with self.assertRaises(OSError):
            self.api.export_snapshot(bound,StalledSink())

    def test_deterministic_bytes(self):
        rows = [record(3,"CN=青岚"),record(2,"CN=z"),record(1,"CN=a")]
        rows[2]["attributes"] = {"objectUUID": ["00000000000000000000000000000001"],
                                 "Tags": ["é","e\u0301","z"], "extra":[]}
        rows[2]["department_path"] = [" A ","B"]
        expected_rows = [
            {"dn":"cn=a","attributes":{"objectuuid":["00000000-0000-0000-0000-000000000001"],
                                     "tags":["z","é"],"extra":[]},"department_path":["A","B"]},
            {"dn":"cn=z","attributes":{"objectuuid":["00000000-0000-0000-0000-000000000002"],
                                     "cn":["Person 2"]},"department_path":None},
            {"dn":"cn=青岚","attributes":{"objectuuid":["00000000-0000-0000-0000-000000000003"],
                                      "cn":["Person 3"]},"department_path":None}]
        expected = b"".join((json.dumps(row,sort_keys=True,ensure_ascii=False,
                                       separators=(",",":"))+"\n").encode()
                            for row in expected_rows)
        ids = [self.api.import_snapshot("forward",rows).snapshot_id,
               self.api.import_snapshot("reverse",reversed(rows)).snapshot_id]
        for sid in ids:
            sink = io.BytesIO()
            result = self.api.export_snapshot(sid,sink)
            self.assertEqual(sink.getvalue(),expected)
            self.assertEqual(result.record_count,3)
            self.assertEqual(result.digest,hashlib.sha256(expected).hexdigest())
            self.assertEqual(self.api.source_info(sid).digest,result.digest)

    def test_lookup_errors(self):
        sid = self.old()
        with self.assertRaises(UnknownSnapshot):
            self.api.get_entry(999,"not a DN")
        with self.assertRaises(UnknownSnapshot):
            self.api.members(999,["A"])
        with self.assertRaises(UnknownSnapshot):
            self.api.export_snapshot(999,io.BytesIO())
        with self.assertRaises(InvalidName):
            self.api.get_entry(sid,"not a DN")
        self.assertIsNone(self.api.get_entry(sid,"cn=absent"))
        entry = self.api.get_entry(sid,"cn=1,ou=People")
        changed = {k:list(v) for k,v in entry.attributes}
        changed["tags"].clear()
        self.assertNotEqual(changed["tags"],list(dict(self.api.get_entry(sid,entry.dn).attributes)["tags"]))
