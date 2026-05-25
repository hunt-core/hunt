from __future__ import annotations

import ipaddress
import json as _json_mod
import re
import uuid as _uuid_mod
from datetime import datetime
from typing import Any

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


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
    if value is None or value == "":
        return "The field is required."
    return None


def _string(value: Any, _params: list, _data: dict) -> str | None:
    if value is not None and not isinstance(value, str):
        return "The field must be a string."
    return None


def _integer(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        int(str(value))
    except ValueError:
        return "The field must be an integer."
    return None


def _numeric(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    try:
        float(str(value))
    except ValueError:
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
    pattern = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, str(value)):
        return "The field must be a valid email address."
    return None


def _min(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    n = float(params[0])
    if isinstance(value, (list, tuple)) and len(value) < n:
        return f"The field must have at least {int(n)} items."
    if isinstance(value, str) and len(value) < n:
        return f"The field must be at least {int(n)} characters."
    if isinstance(value, (int, float)) and value < n:
        return f"The field must be at least {n}."
    return None


def _max(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    n = float(params[0])
    if isinstance(value, (list, tuple)) and len(value) > n:
        return f"The field may not have more than {int(n)} items."
    if isinstance(value, str) and len(value) > n:
        return f"The field may not be greater than {int(n)} characters."
    if isinstance(value, (int, float)) and value > n:
        return f"The field may not be greater than {n}."
    return None


def _in_rule(value: Any, params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if str(value) not in params:
        return f"The selected value is invalid. Valid options: {', '.join(params)}."
    return None


def _not_in(value: Any, params: list, _data: dict) -> str | None:
    if value is None:
        return None
    if str(value) in params:
        return "The selected value is invalid."
    return None


def _confirmed(value: Any, params: list, data: dict) -> str | None:
    if not params:
        return None
    confirmation = data.get(f"{params[0]}_confirmation")
    if value != confirmation:
        return "The field confirmation does not match."
    return None


def _regex(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    pattern = params[0]
    if not re.match(pattern, str(value)):
        return "The field format is invalid."
    return None


def _url(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(str(value))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "The field must be a valid URL."
    return None


def _unique(value: Any, params: list, _data: dict) -> str | None:
    """unique:table,column[,except_id[,id_column]]"""
    if value is None or len(params) < 1:
        return None
    from sqlalchemy import text

    from hunt.database.connection import connection

    table = _validate_ident(params[0], "table")
    column = _validate_ident(params[1] if len(params) > 1 else "id", "column")
    except_id = params[2] if len(params) > 2 else None
    id_column = _validate_ident(params[3] if len(params) > 3 else "id", "id_column")

    engine = connection()
    with engine.connect() as conn:
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
    from sqlalchemy import text

    from hunt.database.connection import connection

    table = _validate_ident(params[0], "table")
    column = _validate_ident(params[1] if len(params) > 1 else "id", "column")
    engine = connection()
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :v"),
            {"v": value},
        )
        count = result.fetchone()[0]
    if count == 0:
        return "The selected value is invalid."
    return None


def _size(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    n = int(params[0])
    if isinstance(value, str) and len(value) != n:
        return f"The field must be {n} characters."
    return None


def _array(value: Any, _params: list, _data: dict) -> str | None:
    if value is not None and not isinstance(value, (list, tuple)):
        return "The field must be an array."
    return None


def _different(value: Any, params: list, data: dict) -> str | None:
    if value is None or not params:
        return None
    other_field = params[0]
    if value == data.get(other_field):
        return f"The field must be different from {other_field}."
    return None


def _required_with(value: Any, params: list, data: dict) -> str | None:
    """required_with:field1,field2 — required if any listed field is present and non-empty."""
    if not params:
        return None
    if any(data.get(p) not in (None, "") for p in params):
        if value is None or value == "":
            return "The field is required."
    return None


def _required_without(value: Any, params: list, data: dict) -> str | None:
    """required_without:field1,field2 — required if any listed field is absent or empty."""
    if not params:
        return None
    if any(data.get(p) in (None, "") for p in params):
        if value is None or value == "":
            return "The field is required."
    return None


def _required_if(value: Any, params: list, data: dict) -> str | None:
    """required_if:other_field,expected_value"""
    if len(params) < 2:
        return None
    other_field, expected = params[0], params[1]
    if str(data.get(other_field, "")) == expected:
        if value is None or value == "":
            return "The field is required."
    return None


def _required_unless(value: Any, params: list, data: dict) -> str | None:
    """required_unless:other_field,value — required unless other_field == value"""
    if len(params) < 2:
        return None
    other_field, expected = params[0], params[1]
    if str(data.get(other_field, "")) != expected:
        if value is None or value == "":
            return "The field is required."
    return None


def _gt(value: Any, params: list, data: dict) -> str | None:
    if value is None or not params:
        return None
    try:
        threshold = float(data.get(params[0], params[0]))
        if float(value) <= threshold:
            return f"The field must be greater than {params[0]}."
    except (ValueError, TypeError):
        return "The field must be greater than the specified value."
    return None


def _gte(value: Any, params: list, data: dict) -> str | None:
    if value is None or not params:
        return None
    try:
        threshold = float(data.get(params[0], params[0]))
        if float(value) < threshold:
            return f"The field must be greater than or equal to {params[0]}."
    except (ValueError, TypeError):
        return "The field must be greater than or equal to the specified value."
    return None


def _lt(value: Any, params: list, data: dict) -> str | None:
    if value is None or not params:
        return None
    try:
        threshold = float(data.get(params[0], params[0]))
        if float(value) >= threshold:
            return f"The field must be less than {params[0]}."
    except (ValueError, TypeError):
        return "The field must be less than the specified value."
    return None


def _lte(value: Any, params: list, data: dict) -> str | None:
    if value is None or not params:
        return None
    try:
        threshold = float(data.get(params[0], params[0]))
        if float(value) > threshold:
            return f"The field must be less than or equal to {params[0]}."
    except (ValueError, TypeError):
        return "The field must be less than or equal to the specified value."
    return None


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


def _before_date(value: Any, params: list, data: dict) -> str | None:
    """before:field_name_or_date"""
    if value is None or not params:
        return None
    raw = data.get(params[0], params[0])
    try:
        dt_val = _parse_date(str(value))
        dt_other = _parse_date(str(raw))
    except ValueError:
        return "The field must be a valid date."
    if dt_val >= dt_other:
        return f"The field must be a date before {params[0]}."
    return None


def _after_date(value: Any, params: list, data: dict) -> str | None:
    """after:field_name_or_date"""
    if value is None or not params:
        return None
    raw = data.get(params[0], params[0])
    try:
        dt_val = _parse_date(str(value))
        dt_other = _parse_date(str(raw))
    except ValueError:
        return "The field must be a valid date."
    if dt_val <= dt_other:
        return f"The field must be a date after {params[0]}."
    return None


def _image(value: Any, _params: list, _data: dict) -> str | None:
    if value is None:
        return None
    from hunt.http.request import UploadedFile

    if not isinstance(value, UploadedFile):
        return "The field must be an uploaded image."
    mime = value.get_mime_type()
    if mime == "image/svg+xml":
        return "SVG uploads are not permitted."
    if not mime.startswith("image/"):
        return "The file must be an image (jpeg, png, gif, webp)."
    return None


def _mimes(value: Any, params: list, _data: dict) -> str | None:
    """mimes:jpg,png,gif — validates file MIME type by extension."""
    if value is None:
        return None
    from hunt.http.request import UploadedFile

    if not isinstance(value, UploadedFile):
        return "The field must be an uploaded file."
    _EXT_MIME = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "pdf": "application/pdf",
        "txt": "text/plain",
        "mp4": "video/mp4",
        "mp3": "audio/mpeg",
    }
    allowed_mimes = {_EXT_MIME.get(ext.lower(), f"application/{ext.lower()}") for ext in params}
    if value.get_mime_type() not in allowed_mimes:
        return f"The file must be of type: {', '.join(params)}."
    return None


def _max_file_size(value: Any, params: list, _data: dict) -> str | None:
    """max_file_size:n — max file size in kilobytes."""
    if value is None or not params:
        return None
    from hunt.http.request import UploadedFile

    if not isinstance(value, UploadedFile):
        return None
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
    "in": _in_rule,
    "not_in": _not_in,
    "confirmed": _confirmed,
    "regex": _regex,
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
    "image": _image,
    "mimes": _mimes,
    "max_file_size": _max_file_size,
}
