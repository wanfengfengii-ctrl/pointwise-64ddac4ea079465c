import unicodedata
from directory.quality.attributes import ATTRIBUTE_NAME


class InvalidName(ValueError):
    pass


class InvalidDepartmentPath(ValueError):
    pass


def normalize_dn(value):
    if not isinstance(value, str) or not value:
        raise InvalidName("DN must be nonempty")
    rdns = []
    name = []
    text = []
    in_value = False
    escaped = False

    def finish():
        key = "".join(name)
        val = unicodedata.normalize("NFC", "".join(text))
        if not in_value or not ATTRIBUTE_NAME.fullmatch(key) or not val:
            raise InvalidName("expected attribute=nonempty-value")
        if val != val.strip() or val.startswith("#"):
            raise InvalidName("unsupported DN whitespace or hex form")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in val):
            raise InvalidName("DN contains control character")
        encoded = "".join("\\" + ch if ch in ",=+\\\";<>"
                          else ch for ch in val)
        rdns.append(key.lower() + "=" + encoded)

    for ch in value:
        if escaped:
            if ch not in ",=+\\\";<>":
                raise InvalidName("unsupported DN escape")
            (text if in_value else name).append(ch)
            escaped = False
        elif ch == "\\":
            if not in_value:
                raise InvalidName("escape in attribute name")
            escaped = True
        elif ch == ",":
            finish()
            name, text, in_value = [], [], False
        elif ch == "=" and not in_value:
            in_value = True
        elif ch in "=+\";<>":
            raise InvalidName("reserved DN character must be escaped")
        else:
            (text if in_value else name).append(ch)
    if escaped:
        raise InvalidName("unfinished DN escape")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise InvalidName("invalid Unicode") from exc
    finish()
    return ",".join(rdns)


def normalize_department_path(path):
    if path is None:
        return None
    if not isinstance(path, (list, tuple)) or not path:
        raise InvalidDepartmentPath("department must be a nonempty segment list")
    result = []
    for part in path:
        if not isinstance(part, str):
            raise InvalidDepartmentPath("department segment must be text")
        part = unicodedata.normalize("NFC", part.strip())
        if not part or any(ord(ch) < 32 or ord(ch) == 127 for ch in part):
            raise InvalidDepartmentPath("invalid department segment")
        try:
            part.encode("utf-8")
        except UnicodeError as exc:
            raise InvalidDepartmentPath("invalid Unicode") from exc
        result.append(part)
    return tuple(result)
