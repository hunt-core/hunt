"""Tagged JSON codec shared by every cache store.

Plain JSON cannot represent ``datetime``, ``date``, ``time``, ``Decimal`` or
``bytes``.  Rather than letting each store improvise (one raising, another
silently stringifying), all stores encode through this module so a value
round-trips identically on the array, file and Redis drivers.

Non-JSON values are wrapped in a single-key-tagged object::

    {"__hunt__": "datetime", "v": "2026-01-01T12:00:00+00:00"}

A user dict that itself contains the tag key is wrapped in a ``"dict"`` tag so
it can never be mistaken for an encoded value.  Values written by older
versions (plain JSON, no tags) decode unchanged.

Unsupported types raise ``TypeError`` on every driver.
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

TAG = "__hunt__"

_Encoder = Callable[[Any], "dict[str, Any] | None"]
_Decoder = Callable[[str, Any], Any]


def to_jsonable(value: Any, extra: _Encoder | None = None) -> Any:
    """Return a JSON-safe structure for *value*.

    *extra* may handle additional types: it receives a value this codec does
    not know and returns ``{"__hunt__": <tag>, "v": <json-safe>}`` or ``None``.
    """
    # bool/int/float/str/None are JSON-native. (bool is checked first only for
    # clarity: it is an int subclass and serialises correctly either way.)
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return {TAG: "float", "v": repr(value)}
        return value
    # datetime must be tested before date (it is a subclass).
    if isinstance(value, _dt.datetime):
        return {TAG: "datetime", "v": value.isoformat()}
    if isinstance(value, _dt.date):
        return {TAG: "date", "v": value.isoformat()}
    if isinstance(value, _dt.time):
        return {TAG: "time", "v": value.isoformat()}
    if isinstance(value, Decimal):
        return {TAG: "decimal", "v": str(value)}
    if isinstance(value, bytes | bytearray):
        return {TAG: "bytes", "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, list | tuple):
        return [to_jsonable(v, extra) for v in value]
    if isinstance(value, set | frozenset):
        items = [to_jsonable(v, extra) for v in value]
        try:
            items.sort(key=lambda item: json.dumps(item, sort_keys=True))
        except TypeError:
            pass
        return {TAG: "set", "v": items}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                if isinstance(k, bool | int | float) or k is None:
                    # Same coercion json.dumps applies to scalar keys.
                    k = json.dumps(k)
                else:
                    raise TypeError(f"Cache values need string dict keys; got {type(k).__name__} key {k!r}.")
            out[k] = to_jsonable(v, extra)
        if TAG in out:
            return {TAG: "dict", "v": out}
        return out
    if extra is not None:
        handled = extra(value)
        if handled is not None:
            return handled
    raise TypeError(
        f"Object of type {type(value).__module__}.{type(value).__qualname__} is not cacheable. "
        "Supported: JSON types, datetime, date, time, Decimal, bytes, set."
    )


def from_jsonable(value: Any, extra: _Decoder | None = None) -> Any:
    """Inverse of :func:`to_jsonable`."""
    if isinstance(value, list):
        return [from_jsonable(v, extra) for v in value]
    if not isinstance(value, dict):
        return value
    tag = value.get(TAG)
    if tag is None or not isinstance(tag, str) or set(value) != {TAG, "v"}:
        return {k: from_jsonable(v, extra) for k, v in value.items()}
    raw = value["v"]
    if tag == "dict":
        return {k: from_jsonable(v, extra) for k, v in raw.items()}
    if tag == "datetime":
        return _dt.datetime.fromisoformat(raw)
    if tag == "date":
        return _dt.date.fromisoformat(raw)
    if tag == "time":
        return _dt.time.fromisoformat(raw)
    if tag == "decimal":
        return Decimal(raw)
    if tag == "bytes":
        return base64.b64decode(raw)
    if tag == "set":
        return {_hashable(from_jsonable(v, extra)) for v in raw}
    if tag == "float":
        return float(raw)
    if extra is not None:
        return extra(tag, raw)
    raise ValueError(f"Unknown cache value tag {tag!r}.")


def _hashable(value: Any) -> Any:
    return tuple(_hashable(v) for v in value) if isinstance(value, list) else value


def dumps(value: Any) -> str:
    """Encode *value* to a JSON string using the tagged codec."""
    return json.dumps(to_jsonable(value), separators=(",", ":"))


def loads(raw: str | bytes | bytearray) -> Any:
    """Decode a JSON string produced by :func:`dumps` (or plain legacy JSON)."""
    return from_jsonable(json.loads(raw))
