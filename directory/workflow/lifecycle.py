from directory.archive.schema import ArchiveDatabase
from directory.archive.snapshots import SnapshotStore
from directory.workflow.catalog import Catalog


class DirectoryRuntime:
    def __init__(self, path):
        self.database = ArchiveDatabase(path)
        try:
            self.store = SnapshotStore(self.database)
            self.catalog = Catalog(self.store)
        except BaseException:
            self.database.close()
            raise

    def __enter__(self):
        self.database.connection
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self.database.close()


def open_runtime(path):
    return DirectoryRuntime(path)
