from __future__ import annotations

import ipaddress
import json as _json_mod
import math
import re
import uuid as _uuid_mod
from datetime import datetime
from typing import Any

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_INT_RE = re.compile(r"[+-]?\d+", re.ASCII)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.ASCII)
_HOST_LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_URL_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f<>\"'`\\^{}|]")
_NUMERIC_RULES = frozenset({"integer", "int", "numeric", "digits", "digits_between"})


class RuleContext:
    """What a rule may need to know beyond the value: where the field is and how it is typed.

    Passed as an optional fourth argument to rule functions that declare one, so custom
    three-argument rules registered in :data:`RULES` keep working.
    """

    __slots__ = ("data", "field", "numeric", "rule_names")

    def __init__(self, field: str = "", data: Any = None, rule_names: Any = ()) -> None:
        self.field = field
        self.data = data if data is not None else {}
        self.rule_names = frozenset(rule_names)
        self.numeric = bool(self.rule_names & _NUMERIC_RULES)

    def other(self, name: str) -> Any:
        """Value of another field, resolved next to this field first, then from the root.

        For ``address.zip`` the name ``city`` means ``address.city``; a name that is not a
        sibling is looked up as an absolute (dotted) path.
        """
        parent, _, _leaf = self.field.rpartition(".")
        if parent:
            found, value = lookup(self.data, f"{parent}.{name}")
            if found:
                return value
        return lookup(self.data, name)[1]


def lookup(data: Any, path: str) -> tuple[bool, Any]:
    """Walk nested dicts/lists by a dotted path. Returns ``(found, value)``."""
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return False, None
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.isdigit() and part.isascii():
            idx = int(part)
            if idx >= len(current):
                return False, None
            current = current[idx]
        else:
            return False, None
    return True, current


def _other(name: str, data: Any, ctx: RuleContext | None) -> Any:
    if ctx is not None:
        return ctx.other(name)
    return lookup(data, name)[1] if isinstance(data, dict) else None


def _is_empty(value: Any) -> bool:
    """Empty for ``required``: missing, blank/whitespace-only text, or an empty list/dict."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if _is_upload(value):
        return value.size == 0
    return False


def _is_upload(value: Any) -> bool:
    from hunt.http.request import UploadedFile

    return isinstance(value, UploadedFile)


def _finite(value: Any) -> float | None:
    """``float(value)`` when it is a finite number, else None. Booleans are not numbers."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        # float() accepts "nan", "inf", "1_000" and similar; none of those are user numbers.
        if not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", value, re.ASCII):
            return None
    elif not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _fmt(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def _measure(value: Any, ctx: RuleContext | None) -> tuple[str, float] | None:
    """What ``min``/``max``/``size``/``between`` compare: ``(kind, amount)``.

    Numbers compare by value. A *string* compares by value only when the field also
    carries ``integer`` or ``numeric`` (form input is always text), otherwise by length.
    Lists and dicts compare by item count and uploaded files by size in kilobytes.
    """
    if isinstance(value, bool):
        return None
    if _is_upload(value):
        return "file", value.size / 1024
    if isinstance(value, (list, tuple, dict)):
        return "array", float(len(value))
    if isinstance(value, (int, float)):
        number = _finite(value)
        return None if number is None else ("number", number)
    if isinstance(value, str):
        if ctx is not None and ctx.numeric:
            number = _finite(value)
            # Not a number: the integer/numeric rule reports that; nothing to compare here.
            return None if number is None else ("number", number)
        return "string", float(len(value))
    return None


def _validate_ident(name: str, label: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier for {label}: {name!r}")
    return name


class ValidationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------


def _required(value: Any, _params: list, _data: dict) -> str | None:
    if _is_empty(value):
        return "The field is required."
    return None


def _string(value: Any, _params: list, _data: dict) -> str | None:
    if value is not None and not isinstance(value, str):
        return "The field must be a string."
    return None


def _integer(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float) or not _INT_RE.fullmatch(str(value)):
        return "The field must be an integer."
    return None


def _numeric(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if _finite(value) is None:
        return "The field must be numeric."
    return None


def _boolean(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if value not in (True, False, 1, 0, "1", "0", "true", "false"):
        return "The field must be a boolean."
    return None


def _email(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _EMAIL_RE.fullmatch(value):
        return "The field must be a valid email address."
    return None


def _bound(params: list, index: int = 0) -> float | None:
    try:
        return _finite(params[index])
    except IndexError:
        return None


def _min(value: Any, params: list, _data: dict, ctx: RuleContext | None = None) -> str | None:
    n = _bound(params)
    if value is None or n is None:
        return None
    measured = _measure(value, ctx)
    if measured is None or measured[1] >= n:
        return None
    kind = measured[0]
    if kind == "array":
        return f"The field must have at least {_fmt(n)} items."
    if kind == "string":
        return f"The field must be at least {_fmt(n)} characters."
    if kind == "file":
        return f"The field must be at least {_fmt(n)} kilobytes."
    return f"The field must be at least {_fmt(n)}."


def _max(value: Any, params: list, _data: dict, ctx: RuleContext | None = None) -> str | None:
    n = _bound(params)
    if value is None or n is None:
        return None
    measured = _measure(value, ctx)
    if measured is None or measured[1] <= n:
        return None
    kind = measured[0]
    if kind == "array":
        return f"The field may not have more than {_fmt(n)} items."
    if kind == "string":
        return f"The field may not be greater than {_fmt(n)} characters."
    if kind == "file":
        return f"The field may not be greater than {_fmt(n)} kilobytes."
    return f"The field may not be greater than {_fmt(n)}."


def _between(value: Any, params: list, _data: dict, ctx: RuleContext | None = None) -> str | None:
    """between:min,max — same measure as ``min``/``max``."""
    low, high = _bound(params, 0), _bound(params, 1)
    if value is None or low is None or high is None:
        return None
    measured = _measure(value, ctx)
    if measured is None or low <= measured[1] <= high:
        return None
    unit = {"array": " items", "string": " characters", "file": " kilobytes"}.get(measured[0], "")
    return f"The field must be between {_fmt(low)} and {_fmt(high)}{unit}."


def _in_rule(value: Any, params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple, dict)) or str(value) not in params:
        return "The selected value is invalid."
    return None


def _not_in(value: Any, params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple, dict)) and str(value) in params:
        return "The selected value is invalid."
    return None


def _confirmed(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    if not params:
        return None
    # The confirmation sits next to the field: ``user.password`` -> ``user.password_confirmation``.
    name = params[0].rpartition(".")[2] if ctx is not None else params[0]
    confirmation = _other(f"{name}_confirmation", data, ctx)
    if value != confirmation:
        return "The field confirmation does not match."
    return None


def _same(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    if value is None or not params:
        return None
    if value != _other(params[0], data, ctx):
        return f"The field must match {params[0]}."
    return None


def _regex(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    if isinstance(value, (list, tuple, dict)) or not re.match(params[0], str(value)):
        return "The field format is invalid."
    return None


def _not_regex(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    if isinstance(value, (list, tuple, dict)) or re.search(params[0], str(value)):
        return "The field format is invalid."
    return None


def _url(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    from urllib.parse import urlsplit

    message = "The field must be a valid URL."
    if not isinstance(value, str) or _URL_FORBIDDEN_RE.search(value):
        return message
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        return message
    if parsed.scheme.lower() not in ("http", "https") or not host:
        return message
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if len(host) > 253 or not all(_HOST_LABEL_RE.fullmatch(label) for label in labels):
        return message
    return None


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float)) or value is None


def _unique(value: Any, params: list, _data: dict) -> str | None:
    """unique:table,column[,except_id[,id_column]]"""
    if value is None or len(params) < 1:
        return None
    if not _is_scalar(value):
        return "The value is invalid."
    from sqlalchemy import text

    from hunt.database.connection import connection, use_connection

    table = _validate_ident(params[0], "table")
    column = _validate_ident(params[1] if len(params) > 1 else "id", "column")
    except_id = params[2] if len(params) > 2 else None
    id_column = _validate_ident(params[3] if len(params) > 3 else "id", "id_column")

    engine = connection()
    with use_connection(engine) as conn:
        if except_id is not None:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :v AND {id_column} != :eid"),
                {"v": value, "eid": except_id},
            )
        else:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :v"),
                {"v": value},
            )
        count = result.fetchone()[0]
    if count > 0:
        return "The value has already been taken."
    return None


def _exists(value: Any, params: list, _data: dict) -> str | None:
    """exists:table,column — value must exist in the table."""
    if value is None or len(params) < 1:
        return None
    if not _is_scalar(value):
        return "The selected value is invalid."
    from sqlalchemy import text

    from hunt.database.connection import connection, use_connection

    table = _validate_ident(params[0], "table")
    column = _validate_ident(params[1] if len(params) > 1 else "id", "column")
    engine = connection()
    with use_connection(engine) as conn:
        result = conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :v"),
            {"v": value},
        )
        count = result.fetchone()[0]
    if count == 0:
        return "The selected value is invalid."
    return None


def _size(value: Any, params: list, _data: dict, ctx: RuleContext | None = None) -> str | None:
    n = _bound(params)
    if value is None or n is None:
        return None
    measured = _measure(value, ctx)
    if measured is None:
        return "The field has an invalid size."
    kind, amount = measured
    if amount == n:
        return None
    if kind == "array":
        return f"The field must contain {_fmt(n)} items."
    if kind == "string":
        return f"The field must be {_fmt(n)} characters."
    if kind == "file":
        return f"The field must be {_fmt(n)} kilobytes."
    return f"The field must be {_fmt(n)}."


def _array(value: Any, _params: list, _data: dict) -> str | None:
    if value is not None and not isinstance(value, (list, tuple)):
        return "The field must be an array."
    return None


def _different(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    if value is None or not params:
        return None
    other_field = params[0]
    if value == _other(other_field, data, ctx):
        return f"The field must be different from {other_field}."
    return None


def _required_with(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    """required_with:field1,field2 — required if any listed field is present and non-empty."""
    if not params:
        return None
    if any(not _is_empty(_other(p, data, ctx)) for p in params):
        if _is_empty(value):
            return "The field is required."
    return None


def _required_without(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    """required_without:field1,field2 — required if any listed field is absent or empty."""
    if not params:
        return None
    if any(_is_empty(_other(p, data, ctx)) for p in params):
        if _is_empty(value):
            return "The field is required."
    return None


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _required_if(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    """required_if:other_field,expected_value"""
    if len(params) < 2:
        return None
    other_field, expected = params[0], params[1]
    if _as_text(_other(other_field, data, ctx)) == expected:
        if _is_empty(value):
            return "The field is required."
    return None


def _required_unless(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    """required_unless:other_field,value — required unless other_field == value"""
    if len(params) < 2:
        return None
    other_field, expected = params[0], params[1]
    if _as_text(_other(other_field, data, ctx)) != expected:
        if _is_empty(value):
            return "The field is required."
    return None


def _threshold(param: str, data: Any, ctx: RuleContext | None) -> float | None:
    """Threshold for gt/gte/lt/lte.

    Whether the parameter is a literal or a field name is decided by its *syntax* alone:
    anything that parses as a number is a literal and the request data is never
    consulted, so a client cannot override ``gt:0`` by sending a field named ``0``.
    """
    literal = _finite(param)
    if literal is not None:
        return literal
    return _finite(_other(param, data, ctx))


def _compare(value: Any, params: list, data: Any, ctx: RuleContext | None, op: str, words: str) -> str | None:
    if value is None or not params:
        return None
    number = _finite(value)
    threshold = _threshold(params[0], data, ctx)
    if number is None or threshold is None:
        # NaN, infinity, non-numbers and unresolvable references never satisfy a comparison.
        return f"The field must be {words} the specified value."
    ok = {
        "gt": number > threshold,
        "gte": number >= threshold,
        "lt": number < threshold,
        "lte": number <= threshold,
    }[op]
    return None if ok else f"The field must be {words} {params[0]}."


def _gt(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    return _compare(value, params, data, ctx, "gt", "greater than")


def _gte(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    return _compare(value, params, data, ctx, "gte", "greater than or equal to")


def _lt(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    return _compare(value, params, data, ctx, "lt", "less than")


def _lte(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    return _compare(value, params, data, ctx, "lte", "less than or equal to")


def _ip(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        ipaddress.ip_address(str(value))
    except ValueError:
        return "The field must be a valid IP address."
    return None


def _ipv4(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        addr = ipaddress.ip_address(str(value))
        if not isinstance(addr, ipaddress.IPv4Address):
            return "The field must be a valid IPv4 address."
    except ValueError:
        return "The field must be a valid IPv4 address."
    return None


def _ipv6(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        addr = ipaddress.ip_address(str(value))
        if not isinstance(addr, ipaddress.IPv6Address):
            return "The field must be a valid IPv6 address."
    except ValueError:
        return "The field must be a valid IPv6 address."
    return None


def _uuid(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        _uuid_mod.UUID(str(value))
    except ValueError:
        return "The field must be a valid UUID."
    return None


def _json(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        _json_mod.loads(str(value))
    except (ValueError, TypeError):
        return "The field must be valid JSON."
    return None


def _starts_with(value: Any, params: list, _data: dict) -> str | None:
    if value is None:
        return None
    s = str(value)
    if not any(s.startswith(p) for p in params):
        return f"The field must start with one of: {', '.join(params)}."
    return None


def _ends_with(value: Any, params: list, _data: dict) -> str | None:
    if value is None:
        return None
    s = str(value)
    if not any(s.endswith(p) for p in params):
        return f"The field must end with one of: {', '.join(params)}."
    return None


def _date(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            datetime.strptime(str(value), fmt)
            return None
        except ValueError:
            pass
    return "The field must be a valid date."


def _date_format(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    fmt = params[0]
    try:
        datetime.strptime(str(value), fmt)
    except ValueError:
        return f"The field must match the date format {fmt}."
    return None


def _date_threshold(param: str, data: Any, ctx: RuleContext | None) -> datetime:
    """Date for before/after: a parameter that parses as a date is a literal, never a field."""
    try:
        return _parse_date(param)
    except ValueError:
        pass
    other = _other(param, data, ctx)
    if isinstance(other, (list, tuple, dict)) or other is None:
        raise ValueError(f"Cannot resolve date reference: {param!r}")
    return _parse_date(str(other))


def _before_date(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    """before:field_name_or_date"""
    if value is None or not params:
        return None
    try:
        dt_val = _parse_date(str(value))
        dt_other = _date_threshold(params[0], data, ctx)
    except ValueError:
        return "The field must be a valid date."
    if dt_val >= dt_other:
        return f"The field must be a date before {params[0]}."
    return None


def _after_date(value: Any, params: list, data: dict, ctx: RuleContext | None = None) -> str | None:
    """after:field_name_or_date"""
    if value is None or not params:
        return None
    try:
        dt_val = _parse_date(str(value))
        dt_other = _date_threshold(params[0], data, ctx)
    except ValueError:
        return "The field must be a valid date."
    if dt_val <= dt_other:
        return f"The field must be a date after {params[0]}."
    return None


def _file(value: Any, _params: list, _data: dict) -> str | None:
    if value is not None and not _is_upload(value):
        return "The field must be an uploaded file."
    return None


def _image(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if not _is_upload(value):
        return "The field must be an uploaded image."
    mime = value.get_mime_type()
    if mime == "image/svg+xml":
        return "SVG uploads are not permitted."
    if not mime.startswith("image/"):
        return "The file must be an image (jpeg, png, gif, webp)."
    return None


# Extensions whose content has a recognisable signature: extension -> detected MIME types.
_SNIFFED_TYPES: dict[str, tuple[str, ...]] = {
    "jpg": ("image/jpeg",),
    "jpeg": ("image/jpeg",),
    "png": ("image/png",),
    "gif": ("image/gif",),
    "webp": ("image/webp",),
    "bmp": ("image/bmp",),
    "ico": ("image/x-icon",),
    "pdf": ("application/pdf",),
    "svg": ("image/svg+xml",),
    "zip": ("application/zip",),
    "mp4": ("video/mp4",),
    "m4a": ("video/mp4",),
    "mov": ("video/mp4",),
    "mp3": ("audio/mpeg",),
}
# ZIP containers that only the filename can tell apart.
_ZIP_BASED = frozenset({"docx", "xlsx", "pptx", "odt", "ods", "odp", "epub"})
# Plain-text formats: no signature, so the filename extension plus "is really text" decides.
_TEXT_BASED = frozenset({"txt", "csv", "tsv", "json", "md", "log", "xml", "html", "htm", "yaml", "yml"})
# Formats that must match by filename as well as by content.
_NEEDS_EXTENSION = _ZIP_BASED | _TEXT_BASED | frozenset({"svg", "zip", "m4a", "mov"})

MIME_EXTENSIONS = frozenset(_SNIFFED_TYPES) | _ZIP_BASED | _TEXT_BASED


def _detect_mime(upload: Any) -> str:
    """Content-based type: the request sniffer plus signatures it does not know (mp4, mp3)."""
    mime = upload.get_mime_type()
    if mime != "application/octet-stream":
        return mime
    head = bytes(upload.content[:16])
    if head[4:8] == b"ftyp":
        return "video/mp4"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06 != 0):
        return "audio/mpeg"
    return mime


def _looks_like_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:8192].decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character cut off at the sample boundary is still text.
        return exc.start >= len(data[:8192]) - 3
    return True


def _upload_extension(upload: Any) -> str:
    name = str(getattr(upload, "filename", "") or "")
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def _matches_extension(upload: Any, ext: str, detected: str) -> bool:
    if ext in _NEEDS_EXTENSION:
        actual = _upload_extension(upload)
        if actual != ext and not ({actual, ext} <= {"htm", "html"} or {actual, ext} <= {"yml", "yaml"}):
            return False
    if ext in _ZIP_BASED:
        return detected == "application/zip"
    if ext in _TEXT_BASED:
        if ext == "xml" and detected == "image/svg+xml":
            return _looks_like_text(upload.content)
        return detected == "application/octet-stream" and _looks_like_text(upload.content)
    return detected in _SNIFFED_TYPES[ext]


def _mimes(value: Any, params: list, _data: dict) -> str | None:
    """mimes:jpg,png,pdf — the file's *content* must be one of the listed types.

    The client-supplied Content-Type is never trusted. Formats without a signature
    (txt, csv, docx, ...) are accepted only when the filename extension matches too.
    """
    if value is None:
        return None
    if not _is_upload(value):
        return "The field must be an uploaded file."
    detected = _detect_mime(value)
    if not any(_matches_extension(value, ext.strip().lower(), detected) for ext in params):
        return f"The file must be of type: {', '.join(params)}."
    return None


def _max_file_size(value: Any, params: list, _data: dict) -> str | None:
    """max_file_size:n — max file size in kilobytes."""
    if value is None or not params:
        return None
    if not _is_upload(value):
        return "The field must be an uploaded file."
    max_kb = int(params[0])
    if value.size > max_kb * 1024:
        return f"The file may not be larger than {max_kb} KB."
    return None


def _parse_date(value: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f"Cannot parse date: {value!r}")


def _text(value: Any) -> str | None:
    """The value as text for character-class rules; None when it is not text or a number."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value)


def _alpha(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if text is None or not text.isalpha():
        return "The field may only contain letters."
    return None


def _alpha_num(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if text is None or not text.isalnum():
        return "The field may only contain letters and numbers."
    return None


def _alpha_dash(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    text = _text(value)
    if text is None or not text or not text.replace("-", "").replace("_", "").isalnum():
        return "The field may only contain letters, numbers, dashes and underscores."
    return None


def _digits(value: Any, params: list, _data: dict) -> str | None:
    """digits:n — exactly n ASCII digits."""
    if value is None or not params:
        return None
    text = _text(value)
    n = int(params[0])
    if text is None or not re.fullmatch(r"\d+", text, re.ASCII) or len(text) != n:
        return f"The field must be {n} digits."
    return None


def _digits_between(value: Any, params: list, _data: dict) -> str | None:
    """digits_between:min,max"""
    if value is None or len(params) < 2:
        return None
    text = _text(value)
    low, high = int(params[0]), int(params[1])
    if text is None or not re.fullmatch(r"\d+", text, re.ASCII) or not low <= len(text) <= high:
        return f"The field must be between {low} and {high} digits."
    return None


def _accepted(value: Any, _params: list, _data: dict) -> str | None:
    if isinstance(value, str):
        value = value.strip().lower()
    if value is True or (not isinstance(value, bool) and value in ("yes", "on", "1", "true", 1)):
        return None
    return "The field must be accepted."


def _declined(value: Any, _params: list, _data: dict) -> str | None:
    if isinstance(value, str):
        value = value.strip().lower()
    if value is False or (not isinstance(value, bool) and value in ("no", "off", "0", "false", 0)):
        return None
    return "The field must be declined."


def _filled(value: Any, _params: list, _data: dict) -> str | None:
    """filled — when the field is present it must not be empty."""
    if value is not None and _is_empty(value):
        return "The field must not be empty."
    return None


# ---------------------------------------------------------------------------
# Parameter checks, run once when a validator is built (a wrong rule definition is a
# programming error and must not wait for the first request that reaches it).
# ---------------------------------------------------------------------------


def _check_pattern(params: list) -> None:
    if not params or params[0] == "":
        raise ValueError("a pattern is required")
    try:
        re.compile(params[0])
    except re.error as exc:
        raise ValueError(f"invalid regular expression {params[0]!r}: {exc}") from exc


def _check_number(count: int = 1):
    def check(params: list) -> None:
        if len(params) < count:
            raise ValueError(f"expects {count} numeric parameter(s)")
        for raw in params[:count]:
            if _finite(raw) is None:
                raise ValueError(f"parameter {raw!r} is not a number")

    return check


def _check_integer(count: int = 1):
    def check(params: list) -> None:
        if len(params) < count:
            raise ValueError(f"expects {count} integer parameter(s)")
        for raw in params[:count]:
            if not re.fullmatch(r"\d+", str(raw), re.ASCII):
                raise ValueError(f"parameter {raw!r} is not a non-negative integer")

    return check


def _check_mimes(params: list) -> None:
    if not params:
        raise ValueError("at least one file extension is required")
    for raw in params:
        if raw.strip().lower() not in MIME_EXTENSIONS:
            raise ValueError(f"unsupported file extension {raw!r} (supported: {', '.join(sorted(MIME_EXTENSIONS))})")


def _check_identifiers(params: list) -> None:
    if not params:
        raise ValueError("a table name is required")
    _validate_ident(params[0], "table")
    if len(params) > 1:
        _validate_ident(params[1], "column")
    if len(params) > 3:
        _validate_ident(params[3], "id_column")


def _check_required(count: int):
    def check(params: list) -> None:
        if len(params) < count:
            raise ValueError(f"expects at least {count} parameter(s)")

    return check


# Rules whose single parameter is taken verbatim (it may contain ":", "," and "|").
RAW_PARAMETER_RULES = frozenset({"regex", "not_regex", "date_format"})

# Rules that run even when the field is absent.
IMPLICIT_RULES = frozenset({"accepted", "declined"})

PARAM_CHECKS: dict[str, Any] = {
    "regex": _check_pattern,
    "not_regex": _check_pattern,
    "min": _check_number(1),
    "max": _check_number(1),
    "size": _check_number(1),
    "between": _check_number(2),
    "digits": _check_integer(1),
    "digits_between": _check_integer(2),
    "max_file_size": _check_integer(1),
    "mimes": _check_mimes,
    "unique": _check_identifiers,
    "exists": _check_identifiers,
    "date_format": _check_required(1),
    "gt": _check_required(1),
    "gte": _check_required(1),
    "lt": _check_required(1),
    "lte": _check_required(1),
    "before": _check_required(1),
    "after": _check_required(1),
    "same": _check_required(1),
    "different": _check_required(1),
    "required_with": _check_required(1),
    "required_without": _check_required(1),
    "required_if": _check_required(2),
    "required_unless": _check_required(2),
    "in": _check_required(1),
    "not_in": _check_required(1),
    "starts_with": _check_required(1),
    "ends_with": _check_required(1),
}


RULES: dict[str, Any] = {
    "required": _required,
    "string": _string,
    "integer": _integer,
    "int": _integer,
    "numeric": _numeric,
    "boolean": _boolean,
    "bool": _boolean,
    "email": _email,
    "min": _min,
    "max": _max,
    "between": _between,
    "in": _in_rule,
    "not_in": _not_in,
    "confirmed": _confirmed,
    "same": _same,
    "regex": _regex,
    "not_regex": _not_regex,
    "url": _url,
    "unique": _unique,
    "exists": _exists,
    "size": _size,
    "array": _array,
    "different": _different,
    "required_with": _required_with,
    "required_without": _required_without,
    "required_if": _required_if,
    "required_unless": _required_unless,
    "gt": _gt,
    "gte": _gte,
    "lt": _lt,
    "lte": _lte,
    "ip": _ip,
    "ipv4": _ipv4,
    "ipv6": _ipv6,
    "uuid": _uuid,
    "json": _json,
    "starts_with": _starts_with,
    "ends_with": _ends_with,
    "date": _date,
    "date_format": _date_format,
    "before": _before_date,
    "after": _after_date,
    "file": _file,
    "image": _image,
    "mimes": _mimes,
    "max_file_size": _max_file_size,
    "alpha": _alpha,
    "alpha_num": _alpha_num,
    "alpha_dash": _alpha_dash,
    "digits": _digits,
    "digits_between": _digits_between,
    "accepted": _accepted,
    "declined": _declined,
    "filled": _filled,
}
