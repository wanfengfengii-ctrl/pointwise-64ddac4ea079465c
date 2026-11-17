from dataclasses import dataclass
from typing import Mapping, Any

RawRecord = Mapping[str, Any]


class SourceFormatError(ValueError):
    pass


class InvalidSource(ValueError):
    pass


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    input_format: str = "records"

    def __post_init__(self):
        if not isinstance(self.source_id, str) or not self.source_id:
            raise InvalidSource("source_id must be a nonempty string")
        if self.input_format not in ("records", "jsonl"):
            raise InvalidSource("unsupported input format")


@dataclass(frozen=True)
class SourceEvidence:
    descriptor: SourceDescriptor
    record_count: int
    completed: bool

    def __post_init__(self):
        if type(self.record_count) is not int or self.record_count < 0:
            raise InvalidSource("invalid record count")
        if type(self.completed) is not bool:
            raise InvalidSource("completed must be bool")


def validate_record(record):
    if not isinstance(record, Mapping):
        raise SourceFormatError("record must be a mapping")
    if set(record) - {"dn", "attributes", "department_path"}:
        raise SourceFormatError("unknown record field")
    if not {"dn", "attributes"} <= set(record):
        raise SourceFormatError("dn and attributes are required")
    return record
