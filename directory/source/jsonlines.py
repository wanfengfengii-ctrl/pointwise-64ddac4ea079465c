import json
from directory.source.model import SourceFormatError, validate_record


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SourceFormatError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _constant(value):
    raise SourceFormatError("non-JSON constant: " + value)


def decode_jsonl_line(data, line_number=1):
    try:
        if not isinstance(data, bytes):
            raise SourceFormatError("JSONL input must be binary")
        if not data.strip():
            raise SourceFormatError("blank record")
        record = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                            parse_constant=_constant)
        return validate_record(record)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise SourceFormatError(f"line {line_number}: {exc}") from exc


class JsonLinesRecords:
    def __init__(self, stream):
        self.stream = stream
        self._started = False

    def __iter__(self):
        if self._started:
            raise SourceFormatError("JSONL records are single-pass")
        self._started = True
        for number, line in enumerate(self.stream, 1):
            yield decode_jsonl_line(line, number)
