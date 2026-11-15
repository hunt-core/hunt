"""JSON-safe serialization for queued mailables and notifications.

Queued mail and notifications used to be pickled into the job payload.  They
are now described explicitly:

* an object is stored as its importable class path plus its instance state,
  encoded with the tagged JSON codec from ``hunt.cache.codec`` (datetime,
  date, Decimal, bytes, ... round-trip);
* a model inside that state is stored as ``(class path, primary key)`` and
  re-fetched in the worker, so no row data (password hashes, tokens) is
  copied into the queue store;
* anything else that is not JSON-safe raises ``TypeError`` at dispatch time,
  naming the offending attribute.

On the worker a class is only imported if it is a subclass of the expected
base class, and it is rebuilt without calling ``__init__``.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from hunt.cache import codec

_MODEL_TAG = "model"


def _model_base() -> type | None:
    try:
        from hunt.database.model import Model
    except Exception:  # pragma: no cover - database layer unavailable
        return None
    return Model


def is_model(value: Any) -> bool:
    base = _model_base()
    return base is not None and isinstance(value, base)


def class_path(cls: type) -> str:
    """Return ``module.QualName`` for *cls*, refusing classes a worker cannot import."""
    if "<locals>" in cls.__qualname__ or cls.__module__ == "__main__":
        raise TypeError(
            f"{cls.__module__}.{cls.__qualname__} cannot be queued: the class must be importable "
            "from a module (not defined inside a function or in __main__)."
        )
    return f"{cls.__module__}.{cls.__qualname__}"


def import_class(path: str, base: type) -> type:
    """Import ``module.QualName`` and require it to be a subclass of *base*."""
    if not isinstance(path, str) or "." not in path:
        raise TypeError(f"Invalid class path in queue payload: {path!r}")
    parts = path.split(".")
    # Try the longest module prefix first so nested classes (Outer.Inner) resolve.
    for split in range(len(parts) - 1, 0, -1):
        try:
            obj: Any = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        try:
            for attr in parts[split:]:
                obj = getattr(obj, attr)
        except AttributeError:
            continue
        if isinstance(obj, type) and issubclass(obj, base):
            return obj
        raise TypeError(f"{path} is not a {base.__name__} subclass; refusing to load it from a queue payload.")
    raise ImportError(f"Cannot import {path!r} named in a queue payload.")


def model_reference(model: Any) -> dict[str, Any]:
    """Describe a model as ``{"class": path, "key": primary key}``."""
    cls = type(model)
    pk_name = getattr(cls, "primary_key", "id")
    attributes = getattr(model, "_attributes", {})
    key = attributes.get(pk_name) if isinstance(attributes, dict) else getattr(model, pk_name, None)
    if key is None:
        raise TypeError(
            f"{cls.__name__} has no primary key yet; save the model before queueing mail or notifications for it."
        )
    return {"class": class_path(cls), "key": codec.to_jsonable(key)}


def load_model(reference: Any) -> Any:
    """Re-fetch the model described by :func:`model_reference`."""
    base = _model_base()
    if base is None or not isinstance(reference, dict):
        raise TypeError(f"Invalid model reference in queue payload: {reference!r}")
    cls = import_class(reference.get("class", ""), base)
    key = codec.from_jsonable(reference.get("key"))
    instance = cls.find(key)  # type: ignore[attr-defined]
    if instance is None:
        raise LookupError(f"{cls.__name__} with primary key {key!r} no longer exists; cannot run the queued job.")
    return instance


def _encode_extra(value: Any) -> dict[str, Any] | None:
    if is_model(value):
        return {codec.TAG: _MODEL_TAG, "v": model_reference(value)}
    return None


def _decode_extra(tag: str, raw: Any) -> Any:
    if tag == _MODEL_TAG:
        return load_model(raw)
    raise ValueError(f"Unknown tag {tag!r} in queue payload.")


def encode_state(obj: Any) -> dict[str, Any]:
    """Encode an object's instance attributes into a JSON-safe dict."""
    state: dict[str, Any] = {}
    for name, value in vars(obj).items():
        try:
            state[name] = codec.to_jsonable(value, _encode_extra)
        except TypeError as exc:
            raise TypeError(
                f"Cannot queue {type(obj).__name__}: attribute {name!r} is not JSON-serializable ({exc}). "
                "Store plain data, a saved model, or an identifier instead."
            ) from exc
    return state


def decode_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise TypeError("Invalid object state in queue payload.")
    return {name: codec.from_jsonable(value, _decode_extra) for name, value in state.items()}


def dump_object(obj: Any) -> dict[str, Any]:
    """Describe *obj* as ``{"class": path, "state": {...}}`` (JSON-safe)."""
    return {"class": class_path(type(obj)), "state": encode_state(obj)}


def load_object(payload: Any, base: type) -> Any:
    """Rebuild an object written by :func:`dump_object`; its class must subclass *base*."""
    if not isinstance(payload, dict):
        raise TypeError("Invalid object in queue payload.")
    cls = import_class(payload.get("class", ""), base)
    obj = cls.__new__(cls)
    obj.__dict__.update(decode_state(payload.get("state", {})))
    return obj


def dump_json(data: dict[str, Any]) -> str:
    """Serialise a payload dict to the JSON string stored on the job."""
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def load_json(payload: Any, what: str) -> dict[str, Any]:
    """Parse a job payload written by :func:`dump_json`.

    Payloads from versions that pickled the object (base64 text, not JSON) are
    refused with a clear error; they are never unpickled.
    """
    data: Any = payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
    if not isinstance(data, dict):
        raise TypeError(
            f"Unsupported queued {what} payload (jobs queued by an older hunt version used pickle "
            f"and are no longer loaded). Re-dispatch the {what}."
        )
    return data
