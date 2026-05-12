from __future__ import annotations

import re
from typing import Any

_IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _validate_ident(name: str, label: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier for {label}: {name!r}")
    return name


class ValidationError(Exception):
    pass


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
    if isinstance(value, str) and len(value) < n:
        return f"The field must be at least {int(n)} characters."
    if isinstance(value, (int, float)) and value < n:
        return f"The field must be at least {n}."
    return None


def _max(value: Any, params: list, _data: dict) -> str | None:
    if value is None or not params:
        return None
    n = float(params[0])
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
    # params[0] is the field name, injected by the validator
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
    if not re.match(r"^https?://", str(value)):
        return "The field must be a valid URL."
    return None


def _unique(value: Any, params: list, _data: dict) -> str | None:
    """unique:table,column[,except_id[,id_column]]

    Checks that `value` does not exist in `table.column`, optionally excluding
    a row by its primary key — useful for update flows where the current record
    should not be flagged as a duplicate of itself.

    Example rule strings:
        "unique:users,email"
        "unique:users,email,42"          (exclude row with id=42)
        "unique:users,email,42,user_id"  (exclude where user_id=42)
    """
    if value is None or len(params) < 1:
        return None
    from hunt.database.connection import connection
    from sqlalchemy import text
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
    "size": _size,
    "array": _array,
}
