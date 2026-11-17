from dataclasses import dataclass
import hashlib
import json
from directory.source.model import validate_record
from directory.quality.attributes import normalize_attributes, attributes_to_mapping
from directory.quality.names import normalize_dn, normalize_department_path
from directory.quality.identity import normalize_uuid, IdentityIndex


class InvalidCandidate(ValueError):
    pass


@dataclass(frozen=True)
class DirectoryEntry:
    dn: str
    uuid: str
    attributes: tuple
    department_path: tuple | None


@dataclass(frozen=True)
class Candidate:
    entries: tuple
    count: int
    digest: str


def entry_record(entry):
    return {"dn": entry.dn,
            "attributes": attributes_to_mapping(entry.attributes),
            "department_path": (list(entry.department_path)
                                if entry.department_path is not None else None)}


def canonical_entry_bytes(entry):
    return (json.dumps(entry_record(entry), ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def content_digest(entries):
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(canonical_entry_bytes(entry))
    return digest.hexdigest()


def normalize_entry(record):
    validate_record(record)
    dn = normalize_dn(record["dn"])
    path = normalize_department_path(record.get("department_path"))
    attributes = dict(normalize_attributes(record["attributes"]))
    # UUID cardinality is checked on the original list, before deduplication.
    raw_uuid = next((v for k, v in record["attributes"].items()
                     if k.lower() == "objectuuid"), [])
    object_uuid = normalize_uuid(raw_uuid)
    attributes["objectuuid"] = (object_uuid,)
    return DirectoryEntry(dn, object_uuid, tuple(sorted(attributes.items())), path)


class CandidateBuilder:
    def __init__(self):
        self._entries = []
        self._identity = IdentityIndex()
        self._state = "collecting"

    def add(self, record):
        if self._state != "collecting":
            raise InvalidCandidate("candidate is not collecting")
        entry = normalize_entry(record)
        self._identity.claim(entry.dn, entry.uuid)
        self._entries.append(entry)

    def finish(self, evidence):
        if self._state != "collecting":
            raise InvalidCandidate("candidate is not collecting")
        if not evidence.completed or evidence.record_count != len(self._entries):
            raise InvalidCandidate("incomplete or inconsistent source evidence")
        entries = tuple(sorted(self._entries, key=lambda e: (e.dn, e.uuid)))
        candidate = Candidate(entries, len(entries), content_digest(entries))
        self._state = "sealed"
        return candidate

    def discard(self):
        self._entries.clear()
        self._identity.clear()
        self._state = "discarded"


def validate_candidate(candidate):
    if not isinstance(candidate, Candidate) or not isinstance(candidate.entries, tuple):
        raise InvalidCandidate("not a sealed candidate")
    builder = CandidateBuilder()
    for entry in candidate.entries:
        if not isinstance(entry, DirectoryEntry) or normalize_entry(entry_record(entry)) != entry:
            raise InvalidCandidate("noncanonical entry")
        builder.add(entry_record(entry))
    ordered = tuple(sorted(candidate.entries, key=lambda e: (e.dn, e.uuid)))
    if ordered != candidate.entries or candidate.count != len(ordered):
        raise InvalidCandidate("invalid candidate ordering or count")
    if candidate.digest != content_digest(ordered):
        raise InvalidCandidate("invalid candidate digest")
