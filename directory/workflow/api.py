from directory.source.jsonlines import JsonLinesRecords
from directory.workflow.lifecycle import open_runtime
from directory.workflow.importer import import_snapshot
from directory.delivery.query import get_entry
from directory.delivery.filter import members
from directory.delivery.export import export_snapshot
from directory.delivery.diff import diff_snapshots


class DirectoryAPI:
    def __init__(self, db):
        self.runtime = open_runtime(db)

    def __enter__(self):
        self.runtime.database.connection
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def import_snapshot(self, source_id, records):
        self.runtime.database.connection
        return import_snapshot(self.runtime.store, source_id, records)

    def import_jsonl(self, source_id, stream):
        self.runtime.database.connection
        return import_snapshot(self.runtime.store, source_id, JsonLinesRecords(stream), "jsonl")

    def bind_snapshot(self, snapshot_id=None):
        return self.runtime.catalog.bind(snapshot_id)

    def get_entry(self, snapshot_id, dn):
        return get_entry(self.runtime.store, snapshot_id, dn)

    def members(self, snapshot_id, department_path, recursive=True):
        return members(self.runtime.store, snapshot_id, department_path, recursive)

    def export_snapshot(self, snapshot_id, sink):
        return export_snapshot(self.runtime.store, snapshot_id, sink)

    def diff_snapshots(self, before, after):
        return diff_snapshots(self.runtime.store, before, after)

    def history(self):
        return self.runtime.catalog.history()

    def source_info(self, snapshot_id):
        return self.runtime.catalog.source_info(snapshot_id)

    def close(self):
        self.runtime.close()
