from __future__ import annotations

import json
import os
import time
import uuid as _uuid_mod
from typing import Any

from hunt.queue._logging import log_error
from hunt.queue.drivers.database import DEFAULT_RETRY_AFTER, _make_payload, _serialize_job, retry_after_from_env
from hunt.queue.job import Job
from hunt.queue.serialization import keep_processed_for, processed_retention

# Seconds between scans of the processing list for jobs without a deadline.
_ORPHAN_SCAN_INTERVAL = 5.0


def _now() -> float:
    return time.time()


def _env_connection() -> dict[str, Any]:
    """The connection settings ``hunt.redis_connection.get_redis()`` would use."""
    return {
        "host": os.environ.get("REDIS_HOST", "127.0.0.1"),
        "port": int(os.environ.get("REDIS_PORT", "6379")),
        "db": int(os.environ.get("REDIS_DB", "0")),
        "password": os.environ.get("REDIS_PASSWORD") or None,
    }


class RedisDriver:
    """Queue driver backed by Redis.

    Install redis-py to use: ``pip install redis``

    Config: host, port, db, password, prefix, retry_after, processed_retention

    Layout, per queue (``<prefix>:<queue>``):

    * ``…``            list of ready payloads (LPUSH to enqueue, pop from the tail)
    * ``…:delayed``    sorted set of payloads scored by the time they become ready
    * ``…:processing`` list of payloads a worker currently has custody of
    * ``…:reserved``   sorted set of in-custody payloads scored by their deadline

    Replay protection, per job id (the signed ``id`` in the payload):

    * ``<prefix>:running:<id>``   lease of the worker running the job
      (``SET NX PX``, expiring with the job's deadline)
    * ``<prefix>:processed:<id>`` the job finished and must never run again
      (``SET NX PX``, kept for ``processed_retention`` seconds)

    Only ``SET`` (with ``NX``/``PX``), ``GET`` and ``DEL`` are used for these.
    A job is marked processed after its handler returned and *before* it
    leaves the processing list: a crash in between redelivers a payload whose
    id is already recorded, and that delivery is dropped. A crash before the
    mark redelivers a job that then runs again (at-least-once).

    A job is never held only in worker memory: ``pop()`` moves it from the
    ready list to the processing list in one atomic command, and every other
    transition writes the destination before removing the source. A worker
    that dies mid-job therefore leaves the payload in the processing list, and
    ``pop()`` on any worker returns it to the ready list once ``retry_after``
    seconds have passed. Where two workers could act on the same payload, a
    command that only one of them can win (``ZREM`` returning 1) decides.

    Delivery is at-least-once: a worker crash at the wrong moment, or a job
    that runs longer than ``retry_after``, can cause a second delivery.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "hunt_queue",
        retry_after: int | None = None,
        client: Any = None,
        processed_retention: int | None = None,
    ) -> None:
        self._connection = {"host": host, "port": port, "db": db, "password": password or None}
        self._config = {**self._connection, "decode_responses": False}
        self._prefix = prefix
        self._client: Any = client
        self._blocking_move: str = "blmove"
        self._last_orphan_scan: dict[str, float] = {}
        # payload -> queue it was popped from, for jobs this process holds.
        self._popped_from: dict[Any, str] = {}
        self.retry_after = max(1, int(retry_after)) if retry_after is not None else retry_after_from_env()
        self.processed_retention = processed_retention
        # job id -> lease token this process holds for it.
        self._leases: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _redis(self) -> Any:
        if self._client is None:
            if self._connection == _env_connection():
                # Same server as the framework's shared client: reuse it, so the
                # queue also honours REDIS_URL and the shared socket timeouts.
                from hunt.redis_connection import get_redis

                self._client = get_redis()
            else:
                try:
                    import redis
                except ImportError as exc:
                    raise RuntimeError(
                        "redis-py is required for the Redis queue driver. Install it: pip install redis"
                    ) from exc
                self._client = redis.Redis(
                    **{k: v for k, v in self._config.items() if v is not None},
                    socket_timeout=10,
                    socket_connect_timeout=5,
                )
        return self._client

    # ------------------------------------------------------------------
    # Keys and payload helpers
    # ------------------------------------------------------------------

    def _queue_key(self, queue: str) -> str:
        return f"{self._prefix}:{queue}"

    def _delayed_key(self, queue: str) -> str:
        return f"{self._prefix}:{queue}:delayed"

    def _processing_key(self, queue: str) -> str:
        return f"{self._prefix}:{queue}:processing"

    def _reserved_key(self, queue: str) -> str:
        return f"{self._prefix}:{queue}:reserved"

    def _attempts_key(self) -> str:
        return f"{self._prefix}:attempts"

    def _failed_key(self) -> str:
        return f"{self._prefix}:failed"

    def _running_key(self, job_uid: str) -> str:
        return f"{self._prefix}:running:{job_uid}"

    def _processed_key(self, job_uid: str) -> str:
        return f"{self._prefix}:processed:{job_uid}"

    def _wrap_payload(self, base_payload: str) -> str:
        """Add a stable UUID to the envelope so attempt counts can be tracked."""
        envelope = json.loads(base_payload)
        envelope["uuid"] = str(_uuid_mod.uuid4())
        return json.dumps(envelope)

    @staticmethod
    def _text(raw: Any) -> str:
        return raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)

    def _uuid_from_bytes(self, raw: bytes | str) -> str:
        try:
            uuid = json.loads(self._text(raw)).get("uuid", "")
            return uuid if isinstance(uuid, str) else ""
        except Exception:
            return ""

    def _queue_of(self, raw: Any) -> str:
        """Queue name recorded inside a payload (``default`` if unreadable)."""
        try:
            envelope = json.loads(self._text(raw))
            body = envelope.get("body")
            body = json.loads(body) if isinstance(body, str) else body
            queue = body.get("queue", "default")
            return queue if isinstance(queue, str) else "default"
        except Exception:
            return "default"

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def push(self, job: Job) -> None:
        payload = self._wrap_payload(_make_payload(_serialize_job(job)))
        self._redis().lpush(self._queue_key(job.queue), payload)

    def later(self, delay: int, job: Job) -> None:
        due = _now() + delay
        body = _serialize_job(job)
        # Signed, so the payload's age is measured from when it becomes due.
        body["available_at"] = int(due)
        payload = self._wrap_payload(_make_payload(body))
        self._redis().zadd(self._delayed_key(job.queue), {payload: due})

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        payload = self._wrap_payload(_make_payload(body_dict))
        self._redis().lpush(self._queue_key(queue), payload)

    # ------------------------------------------------------------------
    # Migration and recovery
    # ------------------------------------------------------------------

    def _migrate_delayed(self, queue: str) -> None:
        """Move any ready delayed jobs into the active queue, each exactly once."""
        client = self._redis()
        delayed, ready, processing = self._delayed_key(queue), self._queue_key(queue), self._processing_key(queue)
        for item in client.zrangebyscore(delayed, 0, _now()):
            # Take custody first so a crash between the next two commands
            # leaves the payload in the processing list instead of nowhere.
            client.lpush(processing, item)
            # Only the worker whose ZREM removes the member may enqueue it.
            if client.zrem(delayed, item) == 1:
                client.lpush(ready, item)
            client.lrem(processing, 1, item)

    def _reclaim_expired(self, queue: str) -> None:
        """Return jobs whose worker died (deadline passed) to the ready list."""
        client = self._redis()
        ready, processing, reserved = self._queue_key(queue), self._processing_key(queue), self._reserved_key(queue)
        now = _now()

        expired = client.zrangebyscore(reserved, 0, now)
        if expired:
            in_custody = set(client.lrange(processing, 0, -1))
            for item in expired:
                # Only the worker whose ZREM removes the deadline may requeue.
                if client.zrem(reserved, item) != 1:
                    continue
                if item not in in_custody:
                    continue  # finished or released meanwhile; just a stale deadline
                client.lpush(ready, item)
                client.lrem(processing, 1, item)

        # A payload in custody without a deadline means a worker died between
        # taking it and recording the deadline (or mid-migration). Give it one
        # so the block above recovers it after retry_after.
        if now - self._last_orphan_scan.get(queue, 0.0) >= _ORPHAN_SCAN_INTERVAL:
            self._last_orphan_scan[queue] = now
            for item in client.lrange(processing, 0, -1):
                if client.zscore(reserved, item) is None:
                    client.zadd(reserved, {item: now + self.retry_after}, nx=True)

    def _take(self, queue: str, timeout: int = 1) -> Any:
        """Atomically move the oldest ready payload to the processing list."""
        client = self._redis()
        ready, processing = self._queue_key(queue), self._processing_key(queue)
        if self._blocking_move == "blmove":
            try:
                return client.blmove(ready, processing, timeout, "RIGHT", "LEFT")
            except AttributeError:
                self._blocking_move = "brpoplpush"  # redis-py < 4
            except Exception as exc:
                if "unknown command" not in str(exc).lower():
                    raise
                self._blocking_move = "brpoplpush"  # Redis server < 6.2
        return client.brpoplpush(ready, processing, timeout)

    # ------------------------------------------------------------------
    # Worker API
    # ------------------------------------------------------------------

    def pop(self, queue: str = "default") -> dict | None:
        self._migrate_delayed(queue)
        self._reclaim_expired(queue)
        item = self._take(queue)
        if item is None:
            return None
        client = self._redis()
        deadline = _now() + self.retry_after
        client.zadd(self._reserved_key(queue), {item: deadline})
        self._popped_from[item] = queue
        uuid = self._uuid_from_bytes(item)
        attempts = 1
        if uuid:
            attempts = int(client.hincrby(self._attempts_key(), uuid, 1))
        return {
            "id": item,
            "queue": queue,
            "attempts": attempts,
            "payload": self._text(item),
            "reserved_until": deadline,
        }

    def _custody_queue(self, item: Any) -> str:
        """Queue a payload was popped from (falls back to the name in its body)."""
        queue = self._popped_from.get(item)
        return queue if queue is not None else self._queue_of(item)

    def _forget(self, item: Any, queue: str) -> None:
        """Drop custody of a payload (all copies) and its deadline."""
        client = self._redis()
        self._popped_from.pop(item, None)
        client.lrem(self._processing_key(queue), 0, item)
        client.zrem(self._reserved_key(queue), item)

    def delete(self, job_id: Any) -> None:
        """Remove a finished job from custody and clean up its attempt counter."""
        self._forget(job_id, self._custody_queue(job_id))
        uuid = self._uuid_from_bytes(job_id)
        if uuid:
            self._redis().hdel(self._attempts_key(), uuid)

    def release(self, job_id: Any, delay: int = 0) -> None:
        """Re-enqueue the job (job_id is the raw payload)."""
        queue = self._custody_queue(job_id)
        # Destination first, then give up custody: never in neither place.
        if delay > 0:
            self._redis().zadd(self._delayed_key(queue), {job_id: _now() + delay})
        else:
            self._redis().lpush(self._queue_key(queue), job_id)
        self._forget(job_id, queue)

    def defer(self, job_id: Any, delay: int = 1) -> None:
        """Put a job back without it counting as an attempt."""
        uuid = self._uuid_from_bytes(job_id)
        self.release(job_id, delay=max(1, delay))
        if uuid:
            self._redis().hincrby(self._attempts_key(), uuid, -1)

    # ------------------------------------------------------------------
    # Replay protection
    # ------------------------------------------------------------------

    def processed_store_ready(self) -> bool:
        """The processed-id store is Redis itself, so it is always there."""
        return True

    def processed_store_hint(self) -> str:
        return "the Redis processed-id store is unavailable"

    def _processed_ttl_ms(self, keep_from: float | None) -> int | None:
        """Lifetime of a processed marker (None = keep forever)."""
        keep = keep_processed_for(self.processed_retention)
        if keep == 0:
            return None
        # A job that ran before it was due is remembered from its due time.
        early = max(0.0, float(keep_from or 0) - _now())
        return int((keep + early) * 1000)

    def _mark_processed(self, job_uid: str, value: str, keep_from: float | None) -> bool:
        client = self._redis()
        key = self._processed_key(job_uid)
        ttl = self._processed_ttl_ms(keep_from)
        if ttl is None:
            stored = client.set(key, value, nx=True)
        else:
            stored = client.set(key, value, nx=True, px=ttl)
        if stored:
            return True
        # Not stored: someone recorded it first, unless that someone was this
        # same call on an earlier, half-finished attempt.
        return self._text(client.get(key) or b"") == value

    def claim_run(self, job_uid: str, job_data: dict | None = None) -> str:
        """Take the right to run *job_uid*.

        Returns ``"claimed"``, ``"done"`` (the id already finished: drop this
        delivery) or ``"busy"`` (another delivery of the id is running right
        now: postpone this one). The lease is taken first and the processed
        marker read second; ``complete()`` writes the marker first and drops
        the lease second, so a job that finished is always seen as done.
        """
        client = self._redis()
        deadline = float((job_data or {}).get("reserved_until") or 0) or (_now() + self.retry_after)
        ttl = max(1000, int((deadline - _now()) * 1000))
        token = _uuid_mod.uuid4().hex
        claimed = bool(client.set(self._running_key(job_uid), token, nx=True, px=ttl))
        if client.get(self._processed_key(job_uid)) is not None:
            if claimed:
                client.delete(self._running_key(job_uid))
            return "done"
        if not claimed:
            return "busy"
        self._leases[job_uid] = token
        return "claimed"

    def release_run(self, job_uid: str) -> None:
        """Give up the lease taken by :meth:`claim_run` (the job will be retried)."""
        token = self._leases.pop(job_uid, None)
        if token is None:
            return
        client = self._redis()
        key = self._running_key(job_uid)
        held = client.get(key)
        # GET then DEL is not atomic; the lease can only change hands in
        # between if it expired at that very moment (the job outlived
        # retry_after), which already means a second delivery.
        if held is not None and self._text(held) == token:
            client.delete(key)

    def complete(self, job_id: Any, job_uid: str, keep_from: float | None = None) -> bool:
        """Record *job_uid* as finished, then remove the job from custody.

        Returns False when another worker had already recorded it (the job
        outlived ``retry_after`` and ran twice).
        """
        token = self._leases.get(job_uid) or _uuid_mod.uuid4().hex
        self._leases[job_uid] = token
        won = self._mark_processed(job_uid, f"done:{token}", keep_from)
        self.delete(job_id)
        self.release_run(job_uid)
        return won

    def prune_processed(self) -> int:
        """Nothing to do: Redis expires processed markers itself (``PX``)."""
        return 0

    def processed_retention_seconds(self) -> int:
        return processed_retention(self.processed_retention)

    def fail(
        self,
        job_id: Any,
        queue: str,
        payload: str,
        exception: str,
        job_uid: str | None = None,
        keep_from: float | None = None,
    ) -> None:
        """Move the failed job to the jobs_failed DB table and clean up attempt tracking.

        If the table cannot be written the job goes to the ``<prefix>:failed``
        Redis list instead; if that fails too the error propagates and the job
        stays reserved, to be retried after ``retry_after``.

        With *job_uid* (the verified id from the payload) the id is recorded
        as finished once the failure is stored, so a copy of the failed
        payload cannot be pushed back and run.
        """
        record = {
            "uuid": str(_uuid_mod.uuid4()),
            "conn": "redis",
            "queue": queue,
            "payload": payload,
            "exc": exception,
            "at": int(time.time()),
        }
        try:
            from hunt.database.connection import raw as db_raw

            db_raw(
                "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
                " VALUES (:uuid, :conn, :queue, :payload, :exc, :at)",
                record,
            )
        except Exception as exc:
            log_error(
                f"Redis queue: could not record failed job in jobs_failed ({exc}); "
                f"storing it in the Redis list {self._failed_key()!r} instead."
            )
            self._redis().lpush(self._failed_key(), json.dumps(record))
        if job_uid is not None:
            token = self._leases.get(job_uid) or _uuid_mod.uuid4().hex
            self._leases[job_uid] = token
            self._mark_processed(job_uid, f"failed:{token}", keep_from)
        self._forget(job_id, self._popped_from.get(job_id, queue))
        uuid = self._uuid_from_bytes(job_id)
        if uuid:
            self._redis().hdel(self._attempts_key(), uuid)
        if job_uid is not None:
            self.release_run(job_uid)

    def size(self, queue: str = "default") -> int:
        """Jobs waiting to run: ready plus delayed (retries are delayed jobs)."""
        client = self._redis()
        return int(client.llen(self._queue_key(queue))) + int(client.zcard(self._delayed_key(queue)))

    def reconnect(self) -> None:
        """Drop pooled connections after an error so the next call reconnects."""
        pool = getattr(self._client, "connection_pool", None)
        if pool is not None and hasattr(pool, "disconnect"):
            pool.disconnect()


__all__ = ["DEFAULT_RETRY_AFTER", "RedisDriver"]
