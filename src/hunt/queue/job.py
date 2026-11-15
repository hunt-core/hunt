from __future__ import annotations

import importlib
import inspect
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_TAG = "__hunt__"
_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")


class JobSerializationError(TypeError):
    """A job cannot be converted to, or rebuilt from, its queue payload."""


def _model_base() -> type | None:
    """Return ``hunt.database.model.Model`` if the ORM has been imported."""
    module = sys.modules.get("hunt.database.model")
    return getattr(module, "Model", None) if module is not None else None


def encode_value(value: Any, where: str) -> Any:
    """Convert *value* into JSON-safe data, or raise naming *where* it lives.

    Models are stored as ``(class path, primary key)`` and re-fetched on the
    worker. ``datetime``, ``date`` and ``Decimal`` are stored as tagged strings.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [encode_value(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise JobSerializationError(
                    f"Cannot queue {where}: dictionary key {key!r} is not a string, so it cannot be stored as JSON."
                )
            if key == _TAG:
                raise JobSerializationError(f"Cannot queue {where}: the key {_TAG!r} is reserved.")
            encoded[key] = encode_value(item, f"{where}[{key!r}]")
        return encoded
    if isinstance(value, datetime):
        return {_TAG: "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {_TAG: "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {_TAG: "decimal", "value": str(value)}
    model_base = _model_base()
    if model_base is not None and isinstance(value, model_base):
        cls = type(value)
        key = getattr(value, "_attributes", {}).get(cls.primary_key)
        if key is None:
            raise JobSerializationError(
                f"Cannot queue {where}: {cls.__name__} has no primary key yet. Save the model before queueing it."
            )
        return {_TAG: "model", "class": f"{cls.__module__}.{cls.__qualname__}", "key": encode_value(key, where)}
    raise JobSerializationError(
        f"Cannot queue {where}: a {type(value).__module__}.{type(value).__qualname__} value is not JSON-serializable. "
        "Store an identifier or plain data instead, or override serialize() and unserialize() on the job."
    )


def _restore_model(dotted_path: str, key: Any) -> Any:
    model_base = _model_base()
    if model_base is None:
        from hunt.database.model import Model as model_base
    if not isinstance(dotted_path, str) or not _DOTTED_PATH_RE.match(dotted_path):
        raise JobSerializationError(f"Cannot restore queued model: malformed class path {dotted_path!r}.")
    module_path, class_name = dotted_path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_path), class_name, None)
    if not (isinstance(cls, type) and issubclass(cls, model_base)):
        raise JobSerializationError(f"Cannot restore queued model: {dotted_path!r} is not a Model class.")
    instance = cls.find(key)
    if instance is None:
        raise LookupError(f"Queued model {dotted_path} with key {key!r} no longer exists.")
    return instance


def decode_value(value: Any) -> Any:
    """Reverse :func:`encode_value`."""
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    if isinstance(value, dict):
        tag = value.get(_TAG)
        if tag is None:
            return {key: decode_value(item) for key, item in value.items()}
        if tag == "datetime":
            return datetime.fromisoformat(value["value"])
        if tag == "date":
            return date.fromisoformat(value["value"])
        if tag == "decimal":
            return Decimal(value["value"])
        if tag == "model":
            return _restore_model(value.get("class", ""), decode_value(value.get("key")))
        raise JobSerializationError(f"Cannot restore queued value: unknown tag {tag!r}.")
    return value


class Job:
    """Base class for queueable jobs.

    A job travels to the worker as JSON. By default every public instance
    attribute is stored (see :meth:`serialize`) and the job is rebuilt by
    calling ``__init__`` with the stored attributes that match its parameters
    (see :meth:`unserialize`). The default ``sync`` driver performs the same
    round-trip, so a job that works in development also works on a worker.
    """

    name: str = ""
    queue: str = "default"
    tries: int = 3
    timeout: int = 60
    # backoff seconds between retries; can be a list for stepped backoff
    backoff: int | list[int] = 0

    def handle(self) -> None:
        raise NotImplementedError

    def failed(self, exc: Exception) -> None:
        """Called when the job exhausts all retry attempts."""

    def chain(self, jobs: list[Job]) -> Job:
        """Set jobs to dispatch sequentially after this one succeeds."""
        self._chain: list[Job] = list(jobs)
        return self

    def serialize(self) -> dict[str, Any]:
        """Return the JSON-safe data needed to rebuild this job on a worker.

        Override together with :meth:`unserialize` for full control. The
        default stores every public attribute; models become identifiers.
        """
        name = type(self).__name__
        return {key: encode_value(value, f"{name}.{key}") for key, value in vars(self).items() if key[:1] != "_"}

    @classmethod
    def unserialize(cls, data: dict[str, Any]) -> Job:
        """Rebuild a job from the data returned by :meth:`serialize`."""
        values = {key: decode_value(value) for key, value in data.items()}
        accepts_any = False
        accepted: set[str] = set()
        required: list[str] = []
        if cls.__init__ is not object.__init__:
            for param in list(inspect.signature(cls.__init__).parameters.values())[1:]:
                if param.kind is inspect.Parameter.VAR_KEYWORD:
                    accepts_any = True
                elif param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                    accepted.add(param.name)
                    if param.default is inspect.Parameter.empty:
                        required.append(param.name)
                elif param.kind is inspect.Parameter.POSITIONAL_ONLY and param.default is inspect.Parameter.empty:
                    required.append(param.name)
        missing = [name for name in required if name not in values]
        if missing:
            raise JobSerializationError(
                f"Cannot rebuild {cls.__name__} from its queue payload: __init__ requires {', '.join(missing)}, "
                "which the job does not store as a public attribute of the same name. Keep constructor arguments "
                "in same-named public attributes, give them defaults, or override serialize() and unserialize()."
            )
        instance = cls(**{key: value for key, value in values.items() if accepts_any or key in accepted})
        for key, value in values.items():
            setattr(instance, key, value)
        return instance

    def dispatch(self) -> None:
        from hunt.queue.manager import Queue

        Queue.push(self)

    def dispatch_later(self, delay: int) -> None:
        """Push the job to the queue with a delay in seconds."""
        from hunt.queue.manager import Queue

        Queue.later(delay, self)

    @classmethod
    def dispatch_now(cls, **kwargs: Any) -> None:
        from hunt.queue.worker import call_handler

        instance = cls(**kwargs)
        call_handler(instance)
