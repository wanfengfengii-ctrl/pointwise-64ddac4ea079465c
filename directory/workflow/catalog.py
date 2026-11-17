class Catalog:
    def __init__(self, store):
        self.store = store

    def bind(self, snapshot_id=None):
        if snapshot_id is None:
            return self.current()
        return self.store.require(snapshot_id).snapshot_id

    def current(self):
        return self.store.current_id()

    def history(self):
        return self.store.list()

    def source_info(self, snapshot_id):
        return self.store.require(snapshot_id)
