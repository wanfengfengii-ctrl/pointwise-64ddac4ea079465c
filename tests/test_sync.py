import sqlite3
import tempfile
import unittest
from pathlib import Path

from directory.archive.schema import ArchiveDatabase
from directory.source import events as ev
from directory.source.events import (Event, ScriptedSource, begin, entry, present,
                                      delete, checkpoint, refresh_done,
                                      seed_records, sample_scripts, reject_commit)
from directory.source.model import InvalidSource
from directory.source.sync import SyncCollector
from directory.quality.identity import DuplicateDN, InvalidUUID
from directory.quality.names import InvalidName, InvalidDepartmentPath
from directory.quality.attributes import InvalidAttributes
from directory.workflow.api import DirectoryAPI

U, V = ev.U, ev.V
W = "00000000-0000-0000-0000-000000000003"


def seed_api(path=":memory:", source_id="seed"):
    api = DirectoryAPI(path)
    api.import_snapshot(source_id, seed_records())
    return api


def seeded_collector(path=":memory:", source_id="ldap-1"):
    api = seed_api(path)
    return api, SyncCollector(api.runtime.database, source_id)


def drive(collector, events, cookie=None):
    source = ScriptedSource(events)
    for event in source.open(
            collector.resume_cookie() if cookie is None else cookie):
        collector.accept(event)
    return source


def attrs(entries, uuid):
    return dict(next(e for e in entries if e.uuid == uuid).attributes)


class TestRefreshSamples(unittest.TestCase):
    """The five hand-computed scripts from SYNC.md."""

    def test_presence_keeps_seed_attributes(self):
        api, collector = seeded_collector()
        drive(collector, sample_scripts()["presence"])
        self.assertEqual(collector.resume_cookie(), b"present-done")
        entries = collector.read()
        self.assertEqual([e.uuid for e in entries], [U])
        self.assertEqual(attrs(entries, U)["x"], ("1",))
        self.assertEqual(entries[0].department_path, ("A",))
        self.assertEqual(entries[0].dn, "cn=a")
        # The mid-refresh checkpoint cookie must never become visible.
        self.assertIsNot(b"ignored", collector.resume_cookie())

    def test_delete_mode_keeps_absent_objects(self):
        api, collector = seeded_collector()
        drive(collector, sample_scripts()["delete-mode"])
        self.assertEqual(collector.resume_cookie(), b"delete-done")
        entries = collector.read()
        self.assertEqual([e.uuid for e in entries], [U, V])
        self.assertEqual(attrs(entries, U)["x"], ("2",))
        self.assertEqual(attrs(entries, V)["y"], ("3",))
        self.assertEqual(next(e for e in entries if e.uuid == V).department_path,
                         ("B",))

    def test_name_swap_reads_old_directory_until_done(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        collector.accept(entry(U, "CN=b", {"objectUUID": [U], "x": ["2"]}, ["B"]))
        # Halfway through the swap, readers still see the old directory.
        self.assertEqual([(e.dn, e.uuid) for e in collector.read()],
                         [("cn=a", U), ("cn=b", V)])
        current = api.bind_snapshot()
        self.assertEqual([e.dn for e in api.members(current, ["B"])], ["cn=b"])
        collector.accept(entry(V, "CN=a", {"objectUUID": [V], "y": ["4"]}, ["A"]))
        collector.accept(refresh_done(b"swap-done"))
        entries = collector.read()
        by_uuid = {e.uuid: e for e in entries}
        self.assertEqual(by_uuid[U].dn, "cn=b")
        self.assertEqual(attrs(entries, U)["x"], ("2",))
        self.assertEqual(by_uuid[U].department_path, ("B",))
        self.assertEqual(by_uuid[V].dn, "cn=a")
        self.assertEqual(attrs(entries, V)["y"], ("4",))
        self.assertEqual(by_uuid[V].department_path, ("A",))
        self.assertEqual(collector.resume_cookie(), b"swap-done")
        api.close()

    def test_incomplete_refresh_discarded_on_disconnect_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sync.db"
            api, collector = seeded_collector(path)
            drive(collector, sample_scripts()["incomplete"])
            # No refresh_done: seed directory stays published, no cookie.
            self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
            self.assertIsNone(collector.resume_cookie())
            collector.disconnect()
            self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
            api.close()
            db = ArchiveDatabase(path)
            reopened = SyncCollector(db, "ldap-1")
            self.assertEqual([e.dn for e in reopened.read()], ["cn=a", "cn=b"])
            self.assertIsNone(reopened.resume_cookie())
            db.close()

    def test_bad_final_name_rejected_and_retryable(self):
        api, collector = seeded_collector()
        seed = collector.read()
        with self.assertRaises(DuplicateDN):
            drive(collector, sample_scripts()["bad-final-name"])
        self.assertEqual(collector.read(), seed)
        self.assertIsNone(collector.resume_cookie())
        with self.assertRaises(ValueError):
            collector.accept(begin("present"))
        collector.disconnect()
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"second-chance"))
        self.assertEqual(collector.read(), seed)
        self.assertEqual(collector.resume_cookie(), b"second-chance")
        api.close()


class TestRefreshSemantics(unittest.TestCase):
    def test_present_mode_drops_unproven_and_resurrectible_entries(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        collector.accept(delete(V))
        collector.accept(present(U))
        # A present after delete cannot resurrect the object.
        with self.assertRaises(ValueError):
            collector.accept(present(V))
        collector.disconnect()
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(entry(V, "CN=b", {"objectUUID": [V], "y": ["5"]}))
        collector.accept(delete(V))
        collector.accept(entry(V, "CN=c", {"objectUUID": [V], "y": ["6"]}))
        collector.accept(refresh_done(b"done"))
        entries = collector.read()
        self.assertEqual([(e.dn, e.uuid) for e in entries],
                         [("cn=a", U), ("cn=c", V)])
        self.assertEqual(attrs(entries, V)["y"], ("6",))
        api.close()

    def test_present_rejects_unknown_uuid(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        with self.assertRaises(ValueError):
            collector.accept(present(W))
        collector.disconnect()
        api.close()

    def test_present_only_in_present_refresh(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        with self.assertRaises(ValueError):
            collector.accept(present(U))
        collector.disconnect()
        # And never in the continuous phase.
        collector.accept(begin("present"))
        collector.accept(refresh_done(b"c"))
        with self.assertRaises(ValueError):
            collector.accept(present(U))
        api.close()

    def test_delete_unknown_uuid_is_noop(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        collector.accept(delete(W))
        collector.accept(refresh_done(b"c"))
        self.assertEqual([e.uuid for e in collector.read()], [U, V])
        api.close()

    def test_repeated_entry_uses_last_content(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        collector.accept(entry(U, "CN=a", {"objectUUID": [U], "x": ["7"]}, ["A"]))
        collector.accept(entry(U, "CN=a", {"objectUUID": [U], "x": ["8"]}, None))
        collector.accept(refresh_done(b"c"))
        entries = collector.read()
        self.assertEqual(attrs(entries, U)["x"], ("8",))
        self.assertIsNone(next(e for e in entries if e.uuid == U).department_path)
        api.close()

    def test_entry_is_full_replacement_including_department(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        collector.accept(entry(U, "CN=a", {"objectUUID": [U], "x": ["1"]}))
        collector.accept(refresh_done(b"c"))
        self.assertIsNone(collector.read()[0].department_path)
        api.close()

    def test_refresh_checkpoint_does_not_publish(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        collector.accept(delete(V))
        collector.accept(checkpoint(b"mid"))
        self.assertIsNone(collector.resume_cookie())
        self.assertEqual([e.uuid for e in collector.read()], [U, V])
        current = api.bind_snapshot()
        self.assertEqual(len(api.history()), 1)
        collector.disconnect()
        self.assertEqual(api.bind_snapshot(), current)
        api.close()

    def test_uuid_mismatch_rejected(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        with self.assertRaises((ValueError, InvalidUUID)):
            collector.accept(entry(U, "CN=a", {"objectUUID": [V], "x": ["1"]}))
        collector.disconnect()
        api.close()

    def test_entry_field_validation_is_immediate(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        bad_entries = (
            entry(U, "cn=x", {"x": ["1"]}),  # missing objectUUID
            entry(U, "CN=a", {"objectUUID": ["not-a-uuid"]}),
            entry(U, "", {"objectUUID": [U]}),
            entry(U, "CN=a", {"objectUUID": [U]}, ["", "A"]),
            entry(U, "CN=a", {"objectUUID": [U, 1]}),
        )
        for bad in bad_entries:
            with self.subTest(bad=bad):
                with self.assertRaises((InvalidName, InvalidUUID,
                                        InvalidDepartmentPath, InvalidAttributes,
                                        ValueError)):
                    collector.accept(bad)
                collector.disconnect()
                collector.accept(begin("present"))
        api.close()

    def test_temporary_duplicate_dn_allowed_during_refresh(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        # Both objects temporarily named CN=b; only resolved at refresh_done.
        collector.accept(entry(U, "CN=b", {"objectUUID": [U], "x": ["2"]}, ["B"]))
        collector.accept(entry(V, "CN=b", {"objectUUID": [V], "y": ["4"]}, ["B"]))
        self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
        with self.assertRaises(DuplicateDN):
            collector.accept(refresh_done(b"c"))
        collector.disconnect()
        self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
        api.close()


class TestContinuousPhase(unittest.TestCase):
    def _started(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(present(V))
        collector.accept(refresh_done(b"start"))
        return api, collector

    def test_entry_and_delete_are_durable_and_visible_to_other_readers(self):
        api, collector = self._started()
        collector.accept(entry(W, "CN=w", {"objectUUID": [W], "z": ["1"]}, ["A"]))
        current = api.bind_snapshot()
        self.assertEqual([e.dn for e in api.members(current, ["A"])],
                         ["cn=a", "cn=w"])
        self.assertEqual(api.get_entry(current, "CN=w").uuid, W)
        collector.accept(delete(U))
        current = api.bind_snapshot()
        self.assertIsNone(api.get_entry(current, "CN=a"))
        self.assertEqual([e.dn for e in collector.read()], ["cn=b", "cn=w"])
        api.close()

    def test_unknown_delete_is_legal_and_creates_no_history(self):
        api, collector = self._started()
        history = api.history()
        collector.accept(delete(W))
        self.assertEqual(api.history(), history)
        self.assertEqual(collector.resume_cookie(), b"start")
        api.close()

    def test_equal_repost_reuses_result(self):
        api, collector = self._started()
        history = api.history()
        collector.accept(entry(U, "CN=a", {"objectUUID": [U], "x": ["1"]}, ["A"]))
        self.assertEqual(api.history(), history)
        api.close()

    def test_checkpoint_advances_cookie_without_new_snapshot(self):
        api, collector = self._started()
        history = api.history()
        collector.accept(checkpoint(b"cp1"))
        self.assertEqual(collector.resume_cookie(), b"cp1")
        self.assertEqual(api.history(), history)
        # Cookie can lag content after a content-only mutation.
        collector.accept(delete(V))
        self.assertEqual(collector.resume_cookie(), b"cp1")
        collector.accept(checkpoint(b"cp2"))
        self.assertEqual(collector.resume_cookie(), b"cp2")
        api.close()

    def test_empty_cookie_is_valid(self):
        api, collector = self._started()
        collector.accept(checkpoint(b""))
        self.assertEqual(collector.resume_cookie(), b"")
        api.close()

    def test_continuous_dn_uniqueness_checked_per_event(self):
        api, collector = self._started()
        with self.assertRaises(DuplicateDN):
            collector.accept(entry(W, "CN=a", {"objectUUID": [W]}))
        # The rejected event changed nothing.
        self.assertEqual([e.uuid for e in collector.read()], [U, V])
        collector.disconnect()
        api.close()

    def test_illegal_continuous_events(self):
        api, collector = self._started()
        for bad in (present(U), refresh_done(b"x"), begin("delete")):
            with self.subTest(bad=bad.kind):
                with self.assertRaises(ValueError):
                    collector.accept(bad)
                collector.disconnect()
                collector.accept(begin("present"))
                collector.accept(refresh_done(b"recovered"))
        api.close()

    def test_empty_directory_publication(self):
        api = DirectoryAPI(":memory:")
        collector = SyncCollector(api.runtime.database, "scope")
        self.assertEqual(collector.read(), ())
        self.assertIsNone(collector.resume_cookie())
        collector.accept(begin("present"))
        collector.accept(refresh_done(b"empty"))
        self.assertEqual(collector.read(), ())
        current = api.bind_snapshot()
        self.assertEqual(api.export_snapshot(current, __import__("io").BytesIO())
                         .record_count, 0)
        self.assertEqual(api.members(current, ["A"]), [])
        api.close()


class TestEventValidation(unittest.TestCase):
    def test_must_begin_first(self):
        api, collector = seeded_collector()
        with self.assertRaises(ValueError):
            collector.accept(entry(U, "CN=a", {"objectUUID": [U]}))
        collector.disconnect()
        api.close()

    def test_non_event_and_unknown_kind(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        with self.assertRaises(ValueError):
            collector.accept({"kind": "entry"})
        collector.disconnect()
        collector.accept(begin("present"))
        with self.assertRaises(ValueError):
            collector.accept(Event("frobnicate"))
        api.close()

    def test_unrelated_fields_are_format_errors(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        cases = (
            Event("begin", mode="present", cookie=b"x"),
            Event("present", uuid=U, dn="CN=a"),
            Event("delete", uuid=U, cookie=b"x"),
            Event("checkpoint", cookie=b"c", uuid=U),
            Event("refresh_done", cookie=b"c", mode="delete"),
            Event("entry", uuid=U, dn="CN=a",
                  attributes={"objectUUID": [U]}, cookie=b"x"),
        )
        for bad in cases:
            with self.subTest(bad=bad.kind):
                with self.assertRaises(ValueError):
                    collector.accept(bad)
                collector.disconnect()
                collector.accept(begin("present"))
        api.close()

    def test_cookie_must_be_bytes(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        for bad_cookie in ("c", 7, ("c",), object()):
            with self.assertRaises(ValueError):
                collector.accept(checkpoint(bad_cookie))
            collector.disconnect()
            collector.accept(begin("present"))
        with self.assertRaises(ValueError):
            collector.accept(refresh_done("nope"))
        api.close()

    def test_invalid_begin_mode(self):
        api, collector = seeded_collector()
        with self.assertRaises(ValueError):
            collector.accept(begin("upsert"))
        self.assertIsNone(collector.resume_cookie())
        # The malformed begin poisoned the session; disconnect clears it.
        collector.disconnect()
        collector.accept(begin("delete"))
        collector.disconnect()
        api.close()

    def test_failed_collector_only_allows_read_cookie_disconnect(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        with self.assertRaises(ValueError):
            collector.accept(present(W))
        collector.read()
        self.assertIsNone(collector.resume_cookie())
        collector.disconnect()
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"ok"))
        self.assertEqual(collector.resume_cookie(), b"ok")
        api.close()

    def test_disconnect_is_idempotent_and_preserves_publication(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"keep"))
        collector.disconnect()
        collector.disconnect()
        self.assertEqual(collector.resume_cookie(), b"keep")
        self.assertEqual(len(collector.read()), 2)
        api.close()

    def test_invalid_source_id(self):
        api = DirectoryAPI(":memory:")
        for bad in ("", None, 7):
            with self.assertRaises((InvalidSource, TypeError)):
                SyncCollector(api.runtime.database, bad)
        api.close()


class TestImmutabilityAndReopen(unittest.TestCase):
    def test_input_mutation_does_not_change_accepted_content(self):
        api, collector = seeded_collector()
        raw_attributes = {"objectUUID": [W], "x": ["1"]}
        raw_path = ["A"]
        collector.accept(begin("delete"))
        collector.accept(entry(W, "CN=w", raw_attributes, raw_path))
        raw_attributes["x"] = ["hijacked"]
        raw_path.append("Hijack")
        collector.accept(refresh_done(b"c"))
        stored = next(e for e in collector.read() if e.uuid == W)
        self.assertEqual(dict(stored.attributes)["x"], ("1",))
        self.assertEqual(stored.department_path, ("A",))
        with self.assertRaises(TypeError):
            stored.attributes[0] = ("x", ("no",))
        api.close()

    def test_read_is_sorted_and_independent_across_calls(self):
        api, collector = seeded_collector()
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(present(V))
        collector.accept(refresh_done(b"c"))
        first = collector.read()
        second = collector.read()
        self.assertEqual(first, second)
        self.assertEqual(first, tuple(sorted(first, key=lambda e: (e.dn, e.uuid))))
        self.assertIsNot(first, second)
        api.close()

    def test_reopen_uses_committed_directory_and_cookie(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reopen.db"
            api, collector = seeded_collector(path)
            collector.accept(begin("present"))
            collector.accept(present(U))
            collector.accept(refresh_done(b"saved"))
            collector.accept(delete(U))
            collector.accept(checkpoint(b"later"))
            api.close()
            db = ArchiveDatabase(path)
            reopened = SyncCollector(db, "ldap-1")
            self.assertEqual(reopened.resume_cookie(), b"later")
            self.assertEqual([e.uuid for e in reopened.read()], [])
            reopened.accept(begin("delete"))
            reopened.accept(refresh_done(b"again"))
            self.assertEqual(reopened.resume_cookie(), b"again")
            db.close()
            db = ArchiveDatabase(path)
            again = SyncCollector(db, "ldap-1")
            self.assertEqual(again.resume_cookie(), b"again")
            db.close()

    def test_reopen_seeds_from_v1_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "seed.db"
            api = seed_api(path)
            api.close()
            db = ArchiveDatabase(path)
            collector = SyncCollector(db, "brand-new-scope")
            self.assertIsNone(collector.resume_cookie())
            self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
            collector.accept(begin("present"))
            collector.accept(present(U))
            collector.accept(present(V))
            collector.accept(refresh_done(b"first"))
            db.close()
            db = ArchiveDatabase(path)
            other = SyncCollector(db, "other-scope")
            # A different source has no cookie but still sees current archive.
            self.assertIsNone(other.resume_cookie())
            self.assertEqual(len(other.read()), 2)
            db.close()


class TestCommitFailure(unittest.TestCase):
    """The reject_commit fixture at both joint-commit points."""

    def test_denied_refresh_done_keeps_previous_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fail.db"
            api, collector = seeded_collector(path)
            collector.accept(begin("present"))
            collector.accept(present(U))
            collector.accept(present(V))
            collector.accept(refresh_done(b"committed"))
            seed_history = api.history()
            current = api.bind_snapshot()
            connection = api.runtime.database.connection
            collector.disconnect()
            with reject_commit(connection) as gate:
                with self.assertRaises(sqlite3.DatabaseError):
                    collector.accept(begin("present"))
                    collector.accept(delete(V))
                    collector.accept(refresh_done(b"denied"))
            self.assertEqual(gate.attempts, gate.denied, 1)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(collector.resume_cookie(), b"committed")
            self.assertEqual(api.bind_snapshot(), current)
            self.assertEqual(api.history(), seed_history)
            self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
            collector.disconnect()
            api.close()
            db = ArchiveDatabase(path)
            reopened = SyncCollector(db, "ldap-1")
            self.assertEqual(reopened.resume_cookie(), b"committed")
            self.assertEqual([e.dn for e in reopened.read()], ["cn=a", "cn=b"])
            db.close()

    def test_denied_continuous_checkpoint_keeps_previous_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fail2.db"
            api, collector = seeded_collector(path)
            collector.accept(begin("delete"))
            collector.accept(refresh_done(b"committed"))
            history = api.history()
            current = api.bind_snapshot()
            connection = api.runtime.database.connection
            with reject_commit(connection) as gate:
                with self.assertRaises(sqlite3.DatabaseError):
                    collector.accept(checkpoint(b"denied"))
            self.assertEqual(gate.attempts, 1)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(collector.resume_cookie(), b"committed")
            self.assertEqual(api.bind_snapshot(), current)
            self.assertEqual(api.history(), history)
            collector.disconnect()
            api.close()
            db = ArchiveDatabase(path)
            reopened = SyncCollector(db, "ldap-1")
            self.assertEqual(reopened.resume_cookie(), b"committed")
            db.close()

    def test_denied_continuous_entry_keeps_previous_state(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"committed"))
        history = api.history()
        connection = api.runtime.database.connection
        with reject_commit(connection):
            with self.assertRaises(sqlite3.DatabaseError):
                collector.accept(entry(W, "CN=w", {"objectUUID": [W]}))
        self.assertFalse(connection.in_transaction)
        self.assertEqual(collector.resume_cookie(), b"committed")
        self.assertEqual(api.history(), history)
        self.assertIsNone(
            api.get_entry(api.bind_snapshot(), "CN=w"))
        collector.disconnect()
        api.close()

    def test_publication_is_a_single_commit(self):
        # Ordinal 2 must never be hit: one joint commit, not split commits.
        api, collector = seeded_collector()
        connection = api.runtime.database.connection
        with reject_commit(connection, ordinal=2) as gate:
            collector.accept(begin("present"))
            collector.accept(present(U))
            collector.accept(refresh_done(b"one-commit"))
        self.assertEqual(gate.attempts, 1)
        self.assertEqual(collector.resume_cookie(), b"one-commit")
        api.close()

    def test_internal_source_ids_stay_unique_after_reuse_then_new_content(self):
        api, collector = seeded_collector()
        connection = api.runtime.database.connection
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(present(V))
        collector.accept(refresh_done(b"c0"))
        # Equal content: snapshot reused, sequence unchanged.
        collector.disconnect()
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(present(V))
        collector.accept(refresh_done(b"c1"))
        # New content: must not collide with the reused label.
        collector.disconnect()
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(refresh_done(b"c2"))
        labels = [row[0] for row in connection.execute(
            "SELECT source_id FROM snapshots WHERE source_id LIKE 'sync:%' ORDER BY id")]
        self.assertEqual(labels, ["sync:ldap-1#0", "sync:ldap-1#1"])
        api.close()


class TestSourcePortCooperation(unittest.TestCase):
    def test_scripted_open_receives_resume_cookie(self):
        api, collector = seeded_collector()
        collector.accept(begin("delete"))
        collector.accept(refresh_done(b"resume-here"))
        collector.disconnect()
        source = drive(collector, [begin("delete"), refresh_done(b"next")])
        self.assertEqual(source.opened_cookies, [b"resume-here"])
        self.assertEqual(collector.resume_cookie(), b"next")
        api.close()

    def test_source_exception_propagates_and_disconnect_recovers(self):
        api, collector = seeded_collector()
        port = ScriptedSource([begin("present"), present(U), present(V),
                               refresh_done(b"x")], failure_at=1,
                              error=OSError("line down"))
        stream = port.open(collector.resume_cookie())
        collector.accept(next(stream))
        with self.assertRaises(OSError):
            next(stream)
        collector.disconnect()
        self.assertEqual([e.dn for e in collector.read()], ["cn=a", "cn=b"])
        collector.accept(begin("present"))
        collector.accept(present(U))
        collector.accept(present(V))
        collector.accept(refresh_done(b"recovered"))
        self.assertEqual(collector.resume_cookie(), b"recovered")
        api.close()

    def test_normal_stop_is_not_refresh_done(self):
        api, collector = seeded_collector()
        port = ScriptedSource([begin("present"), present(U)], stop_at=2)
        for event in port.open(collector.resume_cookie()):
            collector.accept(event)
        # Source stopped cleanly without refresh_done: still refreshing.
        self.assertIsNone(collector.resume_cookie())
        collector.disconnect()
        self.assertEqual(len(collector.read()), 2)
        api.close()


if __name__ == "__main__":
    unittest.main()
