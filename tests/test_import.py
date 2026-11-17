import hashlib
import io
import json
from dataclasses import FrozenInstanceError
from tests import MemoryCase, sample, record, U
from directory.source.model import SourceFormatError, InvalidSource, SourceDescriptor
from directory.source.service import SourceSession
from directory.quality.candidate import CandidateBuilder, InvalidCandidate
from directory.membership.projection import build_projection, validate_projection
from directory.membership.model import DepartmentProjection, DepartmentNode, ProjectionError


class TestImport(MemoryCase):
    def test_complete_directory(self):
        class Once:
            used = False
            def __iter__(self):
                if self.used:
                    raise AssertionError("source traversed twice")
                self.used = True
                return iter(reversed(sample()))
        result = self.api.import_snapshot(" Daily ", Once())
        sid = result.snapshot_id
        self.assertEqual((result.record_count, result.department_count), (5, 5))
        entry = self.api.get_entry(sid, "CN=1,OU=People")
        self.assertEqual(entry.uuid, U)
        self.assertEqual(dict(entry.attributes)["tags"], (" a ", "z", "é"))
        self.assertEqual(dict(entry.attributes)["custom-field"], ())
        self.assertEqual(dict(entry.attributes)["description"], ("青岚",))
        self.assertEqual(entry.department_path, ("A", "B"))
        with self.assertRaises(FrozenInstanceError):
            entry.dn = "cn=other"
        with self.assertRaises(TypeError):
            entry.attributes[0] = ("bad", ())
        info = self.api.source_info(sid)
        output = io.BytesIO()
        export = self.api.export_snapshot(sid, output)
        self.assertEqual((info.source_id, info.input_format, info.completed,
                          info.record_count), (" Daily ", "records", True, 5))
        self.assertEqual(info.digest, hashlib.sha256(output.getvalue()).hexdigest())
        self.assertEqual(export.digest, info.digest)
        actual = [e.dn for e in self.api.members(sid, ["A"])]
        expected = ["cn=1,ou=People", "cn=2,ou=People", "cn=5,ou=People"]
        self.assertEqual(actual, expected)
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v1", "input": {"departments": [["A","B"],["A","BC"],["AB"],None,["A","B","C"]]},
            "expected": {"records": 5, "departments": 5},
            "actual": {"records": result.record_count, "departments": result.department_count},
            "downstream_expected": expected, "downstream_actual": actual,
            "code_paths": ["directory/workflow/importer.py", "directory/source/reader.py",
                           "directory/quality/candidate.py", "directory/membership/projection.py",
                           "directory/archive/snapshots.py", "directory/delivery/filter.py"],
            "oracle": "five hand-enumerated people; segment-prefix membership"}))

    def test_empty_directory(self):
        result = self.api.import_snapshot("empty", iter(()))
        self.assertEqual((result.record_count, result.department_count), (0, 0))
        self.assertEqual(self.api.members(result.snapshot_id, ["A"]), [])
        out = io.BytesIO()
        exported = self.api.export_snapshot(result.snapshot_id, out)
        self.assertEqual(out.getvalue(), b"")
        self.assertEqual(exported.digest, hashlib.sha256(b"").hexdigest())

    def test_source_failure(self):
        old = self.old()
        sentinel = RuntimeError("upstream interrupted")
        def broken():
            yield record(10)
            yield record(11)
            raise sentinel
        with self.assertRaises(RuntimeError) as caught:
            self.api.import_snapshot("retry", broken())
        self.assertIs(caught.exception, sentinel)
        self.assertUnchanged(old)
        self.assertIsNone(self.api.runtime.store.find_source("retry"))
        for malformed in (b'{"dn":', b'{"dn":"\xe4', b"\n",
                          b'{"dn":"a","dn":"b","attributes":{}}\n',
                          b'[]\n', b'{"dn":"cn=x","attributes":{},"extra":1}\n',
                          b'{"dn":NaN,"attributes":{}}\n'):
            with self.subTest(data=malformed), self.assertRaises(SourceFormatError):
                self.api.import_jsonl("retry", io.BytesIO(malformed))
            self.assertUnchanged(old)
        valid = json.dumps(record(10)).encode()
        sid = self.api.import_jsonl("retry", io.BytesIO(valid)).snapshot_id
        self.assertEqual(self.api.source_info(sid).input_format, "jsonl")
        self.assertEqual(len(self.api.history()), 2)

    def test_completion_gate_and_projection(self):
        session = SourceSession("once", sample())
        builder = CandidateBuilder()
        with self.assertRaises(InvalidSource):
            session.finish()
        for row in session.records():
            builder.add(row)
        candidate = builder.finish(session.finish())
        with self.assertRaises(InvalidCandidate):
            builder.add(record(8))
        projection = build_projection(candidate)
        expected = {
            ("A",): ((), ("cn=1,ou=People","cn=2,ou=People","cn=5,ou=People")),
            ("A","B"): (("cn=1,ou=People",), ("cn=1,ou=People","cn=5,ou=People")),
            ("A","B","C"): (("cn=5,ou=People",), ("cn=5,ou=People",)),
            ("A","BC"): (("cn=2,ou=People",), ("cn=2,ou=People",)),
            ("AB",): (("cn=3,ou=People",), ("cn=3,ou=People",))}
        self.assertEqual({n.path:(n.direct_dns,n.recursive_dns) for n in projection.nodes},
                         expected)
        node = projection.nodes[0]
        broken = DepartmentNode(node.path,node.parent,node.children,(),("cn=4,ou=People",))
        with self.assertRaises(ProjectionError):
            validate_projection(candidate, DepartmentProjection((broken,) + projection.nodes[1:]))
        builder.discard()
        session.close()
        with self.assertRaises(InvalidSource):
            session.records()
        with self.assertRaises(InvalidSource):
            SourceDescriptor("")
        self.assertEqual(self.api.history(), ())

    def test_source_port(self):
        from directory.source.events import begin, present, refresh_done, ScriptedSource
        events = [begin("present"), present(U), refresh_done(b"c")]
        port = ScriptedSource(events, stop_at=2)
        self.assertEqual(list(port.open(b"old")), events[:2])
        self.assertEqual(port.opened_cookies, [b"old"])
        error = OSError("source unavailable")
        port = ScriptedSource(events, failure_at=1, error=error)
        stream = port.open(None)
        self.assertEqual(next(stream), events[0])
        with self.assertRaises(OSError) as caught:
            next(stream)
        self.assertIs(caught.exception, error)
