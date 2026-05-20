from __future__ import annotations

import json
import time
import uuid as _uuid_mod
from typing import Any

from hunt.queue.drivers.database import _make_payload, _serialize_job
from hunt.queue.job import Job


class RedisDriver:
    """Queue driver backed by Redis.

    Install redis-py to use: ``pip install redis``

    Config: host, port, db, password, prefix
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "hunt_queue",
    ) -> None:
        self._config = {
            "host": host,
            "port": port,
            "db": db,
            "password": password,
            "decode_responses": False,
        }
        self._prefix = prefix
        self._client: Any = None

    def _redis(self) -> Any:
        if self._client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError(
                    "redis-py is required for the Redis queue driver. Install it: pip install redis"
                ) from exc
            self._client = redis.Redis(**{k: v for k, v in self._config.items() if v is not None})
        return self._client

    def _queue_key(self, queue: str) -> str:
        return f"{self._prefix}:{queue}"

    def _delayed_key(self, queue: str) -> str:
        return f"{self._prefix}:{queue}:delayed"

    def _attempts_key(self) -> str:
        return f"{self._prefix}:attempts"

    def _wrap_payload(self, base_payload: str) -> bytes:
        """Add a stable UUID to the envelope so attempt counts can be tracked."""
        envelope = json.loads(base_payload)
        envelope["uuid"] = str(_uuid_mod.uuid4())
        return json.dumps(envelope).encode()

    def push(self, job: Job) -> None:
        payload = self._wrap_payload(_make_payload(_serialize_job(job)))
        self._redis().lpush(self._queue_key(job.queue), payload)

    def later(self, delay: int, job: Job) -> None:
        payload = self._wrap_payload(_make_payload(_serialize_job(job)))
        score = time.time() + delay
        self._redis().zadd(self._delayed_key(job.queue), {payload: score})

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        base = _make_payload(body_dict)
        envelope = json.loads(base)
        envelope.setdefault("uuid", str(_uuid_mod.uuid4()))
        payload = json.dumps(envelope).encode()
        self._redis().lpush(self._queue_key(queue), payload)

    def _migrate_delayed(self, queue: str) -> None:
        """Move any ready delayed jobs into the active queue."""
        now = time.time()
        key = self._delayed_key(queue)
        items = self._redis().zrangebyscore(key, 0, now)
        for item in items:
            self._redis().lpush(self._queue_key(queue), item)
            self._redis().zrem(key, item)

    def _uuid_from_bytes(self, raw: bytes | str) -> str:
        try:
            data = raw if isinstance(raw, str) else raw.decode()
            return json.loads(data).get("uuid", "")
        except Exception:
            return ""

    def pop(self, queue: str = "default") -> dict | None:
        self._migrate_delayed(queue)
        result = self._redis().brpop(self._queue_key(queue), timeout=1)
        if result is None:
            return None
        _, raw_bytes = result
        payload_str = raw_bytes.decode() if isinstance(raw_bytes, bytes) else raw_bytes
        uuid = self._uuid_from_bytes(raw_bytes)
        attempts = 1
        if uuid:
            attempts = int(self._redis().hincrby(self._attempts_key(), uuid, 1))
        return {
            "id": raw_bytes,
            "queue": queue,
            "attempts": attempts,
            "payload": payload_str,
        }

    def delete(self, job_id: Any) -> None:
        """Clean up the attempt counter for this job."""
        uuid = self._uuid_from_bytes(job_id if isinstance(job_id, bytes) else str(job_id).encode())
        if uuid:
            self._redis().hdel(self._attempts_key(), uuid)

    def release(self, job_id: Any, delay: int = 0) -> None:
        """Re-enqueue the job (job_id is the raw payload bytes)."""
        raw_bytes = job_id if isinstance(job_id, bytes) else str(job_id).encode()
        payload_str = raw_bytes.decode()
        try:
            envelope = json.loads(payload_str)
            body_str = envelope.get("body", payload_str)
            body = json.loads(body_str) if isinstance(body_str, str) else body_str
            queue = body.get("queue", "default")
        except Exception:
            queue = "default"

        if delay > 0:
            score = time.time() + delay
            self._redis().zadd(self._delayed_key(queue), {raw_bytes: score})
        else:
            self._redis().lpush(self._queue_key(queue), raw_bytes)

    def fail(self, job_id: Any, queue: str, payload: str, exception: str) -> None:
        """Move the failed job to the jobs_failed DB table and clean up attempt tracking."""
        try:
            from hunt.database.connection import raw as db_raw

            db_raw(
                "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
                " VALUES (:uuid, :conn, :queue, :payload, :exc, :at)",
                {
                    "uuid": str(_uuid_mod.uuid4()),
                    "conn": "redis",
                    "queue": queue,
                    "payload": payload,
                    "exc": exception,
                    "at": int(time.time()),
                },
            )
        except Exception:
            pass
        self.delete(job_id)

    def size(self, queue: str = "default") -> int:
        return self._redis().llen(self._queue_key(queue))
