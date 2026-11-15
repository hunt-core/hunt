"""Queue payload format shared by every driver and the worker.

A payload is ``{"body": <json string>, "signature": <hex hmac>}``. The body is
signed with a key derived from APP_KEY for the queue only (purpose
``queue.v1``), and carries a unique ``id``, a ``created_at`` timestamp and, for
a delayed job, the ``available_at`` time it becomes due.

Replay protection rests on two rules that belong together:

* a worker records the ``id`` of every job that finished (see the drivers'
  ``complete()``/``fail()``) and never runs a recorded id again;
* a payload is refused as stale once it is older than the time those records
  are kept, measured from when the job became due. A recorded id may
  therefore be forgotten after the retention window: by then every payload
  carrying it is too old to be accepted.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import time
import uuid
from typing import Any

from hunt.queue.job import Job, JobSerializationError

SIGNING_PURPOSE = "queue.v1"

# Seconds a finished job id is remembered (and the default maximum payload age).
DEFAULT_PROCESSED_RETENTION = 7 * 24 * 60 * 60

_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> float:
    return time.time()


class InvalidPayload(ValueError):
    """The payload cannot be trusted or parsed; the job must not be retried."""


class StalePayload(InvalidPayload):
    """The payload is correctly signed but too old to be accepted."""


def valid_job_id(value: Any) -> bool:
    """True when *value* is a job id this framework could have issued."""
    return isinstance(value, str) and bool(_JOB_ID_RE.match(value))


def derive_job_id(purpose: str, parent_id: str) -> str:
    """Deterministic id for a job that follows from another one.

    Used for the next job of a chain and for the retry of a failed job, so
    that producing the follow-up twice (a redelivered parent, the same failed
    row retried twice) yields the same id and it still runs only once.
    """
    return hashlib.sha256(f"hunt.queue.{purpose}:{parent_id}".encode()).hexdigest()[:32]


def serialize_job(job: Job) -> dict:
    """Build the body dict for a job."""
    data = job.serialize()
    if not isinstance(data, dict):
        raise JobSerializationError(f"{type(job).__name__}.serialize() must return a dict, got {type(data).__name__}.")
    body = {
        "class": f"{type(job).__module__}.{type(job).__name__}",
        "data": data,
        "chain": [serialize_job(chained) for chained in getattr(job, "_chain", [])],
        "queue": job.queue,
        "tries": getattr(job, "tries", 3),
        "backoff": getattr(job, "backoff", 0),
        "timeout": getattr(job, "timeout", 60),
    }
    return body


def dump_body(body_dict: dict) -> str:
    """Return the canonical JSON for a body, naming the job when it is not JSON-safe."""
    try:
        return json.dumps(body_dict, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise JobSerializationError(
            f"Cannot queue {body_dict.get('class', 'job')}: its serialized data is not JSON-safe ({exc})."
        ) from exc


def make_payload(body_dict: dict) -> str:
    """Stamp, serialize and sign a body dict."""
    from hunt.security.signing import sign

    stamped = dict(body_dict)
    stamped.setdefault("id", uuid.uuid4().hex)
    stamped.setdefault("created_at", int(_now()))
    body = dump_body(stamped)
    return json.dumps({"body": body, "signature": sign(body, purpose=SIGNING_PURPOSE)})


def _max_age() -> int:
    try:
        return max(0, int(os.environ.get("QUEUE_MAX_JOB_AGE", "0") or 0))
    except ValueError:
        return 0


def processed_retention(configured: Any = None) -> int:
    """Seconds a finished job id is remembered; 0 means "never forget".

    *configured* is ``processed_retention`` from ``config/queue.py``; without
    it ``QUEUE_PROCESSED_RETENTION`` applies, then the 7-day default.
    """
    value = configured if configured is not None else os.environ.get("QUEUE_PROCESSED_RETENTION")
    if value is None or value == "":
        return DEFAULT_PROCESSED_RETENTION
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_PROCESSED_RETENTION


def max_payload_age(retention: Any = None) -> int:
    """Oldest payload a worker accepts, in seconds since the job became due (0 = no limit).

    ``QUEUE_MAX_JOB_AGE`` is an explicit override; otherwise the processed-id
    retention applies.
    """
    return _max_age() or processed_retention(retention)


def keep_processed_for(retention: Any = None) -> int:
    """Seconds a finished id must be kept so that pruning is safe (0 = forever).

    Never shorter than the age up to which payloads are accepted.
    """
    keep = processed_retention(retention)
    if keep == 0:
        return 0
    return max(keep, _max_age())


def due_time(body: dict) -> float:
    """When the job became (or becomes) due: its signed ``available_at``, else ``created_at``."""
    times = [body.get(key) for key in ("created_at", "available_at")]
    times = [float(value) for value in times if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return max(times) if times else 0.0


def open_payload(payload: Any, *, allow_legacy: bool = False, check_age: bool = True, retention: Any = None) -> dict:
    """Verify a payload's signature and return its body dict.

    Raises :class:`InvalidPayload` with a reason fit for the failed-jobs table.
    ``allow_legacy`` also accepts the pre-``queue.v1`` signature (APP_KEY used
    directly); only ``queue:retry`` uses it, to re-sign old failed jobs.

    With ``check_age`` a payload that became due longer ago than
    :func:`max_payload_age` raises :class:`StalePayload`. *retention* is the
    driver's ``processed_retention`` (``None`` reads the environment).
    """
    from hunt.security.signing import verify

    if isinstance(payload, bytes):
        try:
            payload = payload.decode()
        except UnicodeDecodeError as exc:
            raise InvalidPayload("Job payload is not valid UTF-8.") from exc
    try:
        envelope = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise InvalidPayload("Job payload is not valid JSON.") from exc
    if not isinstance(envelope, dict) or "body" not in envelope or "signature" not in envelope:
        raise InvalidPayload("Job payload is missing its signature envelope; unsigned payloads are rejected.")
    body_str, signature = envelope["body"], envelope["signature"]
    if not isinstance(body_str, str) or not isinstance(signature, str):
        raise InvalidPayload("Job payload has a malformed signature envelope.")

    if not verify(body_str, signature, purpose=SIGNING_PURPOSE):
        if verify(body_str, signature):
            if not allow_legacy:
                raise InvalidPayload(
                    "Job payload was signed with the legacy queue signature used before purpose-bound signing "
                    "(queue.v1). It was enqueued before the upgrade; run `hunt queue:retry` to re-sign it."
                )
        else:
            raise InvalidPayload(
                "Job payload signature mismatch: the payload was tampered with or signed with a different APP_KEY."
            )
    try:
        body = json.loads(body_str)
    except ValueError as exc:
        raise InvalidPayload("Job payload body is not valid JSON.") from exc
    if not isinstance(body, dict):
        raise InvalidPayload("Job payload body is not an object.")

    if check_age:
        max_age = max_payload_age(retention)
        due = due_time(body)
        if max_age and due and _now() - due > max_age:
            if _max_age():
                raise StalePayload(
                    f"Job payload is older than QUEUE_MAX_JOB_AGE ({max_age}s); refusing a stale payload."
                )
            raise StalePayload(
                f"Job payload became due more than {max_age}s ago, which is longer than finished job ids are "
                "remembered (QUEUE_PROCESSED_RETENTION), so it can no longer be told apart from a replay; "
                "refusing a stale payload. Run `hunt queue:retry` to run it again."
            )
    return body


def import_job_class(dotted_path: Any, job_cls: type = Job) -> type:
    """Import a dotted class path, validating format and Job subclass constraint."""
    if not isinstance(dotted_path, str) or not _DOTTED_PATH_RE.match(dotted_path):
        raise ValueError(f"Malformed job class path: {dotted_path!r}")
    module_path, class_name = dotted_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    if not (isinstance(cls, type) and issubclass(cls, job_cls)):
        raise TypeError(f"Refusing to execute non-Job class: {dotted_path!r}")
    return cls


def rebuild_job(cls: type, data: Any) -> Job:
    """Rebuild a job instance of *cls* from the ``data`` of a body dict."""
    if not isinstance(data, dict):
        raise JobSerializationError(f"Cannot rebuild {cls.__name__}: payload data is not an object.")
    return cls.unserialize(data)


def round_trip(job: Job) -> Job:
    """Serialize *job* to JSON and rebuild it, exactly as a worker would.

    Used by the sync driver so the serialization contract is enforced in
    development. The class object is reused (not re-imported) so jobs defined
    in function scope keep working.
    """
    body = json.loads(dump_body(serialize_job(job)))
    return rebuild_job(type(job), body.get("data", {}))
