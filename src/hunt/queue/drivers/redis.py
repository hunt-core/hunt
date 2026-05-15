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

    def _failed_key(self) -> str:
        return f"{self._prefix}:failed"

    def push(self, job: Job) -> None:
        body = _serialize_job(job)
        payload = _make_payload(body)
        self._redis().lpush(self._queue_key(job.queue), payload.encode())

    def later(self, delay: int, job: Job) -> None:
        body = _serialize_job(job)
        payload = _make_payload(body)
        score = time.time() + delay
        self._redis().zadd(self._delayed_key(job.queue), {payload.encode(): score})

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        payload = _make_payload(body_dict)
        self._redis().lpush(self._queue_key(queue), payload.encode())

    def _migrate_delayed(self, queue: str) -> None:
        """Move any ready delayed jobs into the active queue."""
        now = time.time()
        key = self._delayed_key(queue)
        items = self._redis().zrangebyscore(key, 0, now)
        for item in items:
            self._redis().lpush(self._queue_key(queue), item)
            self._redis().zrem(key, item)

    def pop(self, queue: str = "default") -> dict | None:
        self._migrate_delayed(queue)
        result = self._redis().brpop(self._queue_key(queue), timeout=1)
        if result is None:
            return None
        _, raw_bytes = result
        payload_str = raw_bytes.decode() if isinstance(raw_bytes, bytes) else raw_bytes
        return {
            "id": raw_bytes,  # used for nack (re-enqueue raw payload)
            "queue": queue,
            "attempts": 1,
            "payload": payload_str,
        }

    def delete(self, job_id: Any) -> None:
        pass  # already consumed by BRPOP

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
        """Store the failed job in a Redis sorted set (score = failed_at timestamp)."""
        entry = json.dumps(
            {
                "uuid": str(_uuid_mod.uuid4()),
                "queue": queue,
                "payload": payload,
                "exception": exception,
                "failed_at": int(time.time()),
            }
        )
        self._redis().zadd(self._failed_key(), {entry.encode(): time.time()})

    def size(self, queue: str = "default") -> int:
        return self._redis().llen(self._queue_key(queue))
