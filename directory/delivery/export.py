from dataclasses import dataclass
import hashlib
from directory.quality.candidate import canonical_entry_bytes
from directory.archive.entries import iter_entries


@dataclass(frozen=True)
class ExportResult:
    record_count: int
    digest: str


def export_snapshot(store, snapshot_id, sink):
    store.require(snapshot_id)
    digest = hashlib.sha256()
    count = 0
    entries = iter_entries(store.connection, snapshot_id)
    try:
        for entry in entries:
            data = canonical_entry_bytes(entry)
            offset = 0
            while offset < len(data):
                written = sink.write(data[offset:])
                if type(written) is not int or not 0 < written <= len(data) - offset:
                    raise OSError("sink must report positive bytes written")
                digest.update(data[offset:offset + written])
                offset += written
            count += 1
    finally:
        entries.close()
    return ExportResult(count, digest.hexdigest())
