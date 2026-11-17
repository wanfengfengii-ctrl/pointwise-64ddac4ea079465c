from dataclasses import dataclass
from directory.source.service import SourceSession
from directory.quality.candidate import CandidateBuilder
from directory.membership.projection import build_projection


@dataclass(frozen=True)
class ImportResult:
    snapshot_id: int
    record_count: int
    department_count: int


def import_snapshot(store, source_id, records, input_format="records"):
    session = SourceSession(source_id, records, input_format)
    builder = CandidateBuilder()
    try:
        for record in session.records():
            builder.add(record)
        evidence = session.finish()
        candidate = builder.finish(evidence)
        projection = build_projection(candidate)
        info = store.publish(candidate, projection, evidence)
        return ImportResult(info.snapshot_id, info.record_count, info.department_count)
    finally:
        builder.discard()
        session.close()
