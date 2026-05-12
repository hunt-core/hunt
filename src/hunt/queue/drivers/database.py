from __future__ import annotations

import json
import time
from typing import Any

from hunt.queue.job import Job


class DatabaseDriver:
    """Stores jobs in a `jobs` table. Run `hunt queue:work` to process."""

    def push(self, job: Job) -> None:
        from hunt.database.connection import raw
        from hunt.security.signing import sign
        body = json.dumps({
            "class": f"{type(job).__module__}.{type(job).__name__}",
            "data": vars(job),
        }, sort_keys=True)
        payload = json.dumps({"body": body, "signature": sign(body)})
        raw(
            "INSERT INTO jobs (queue, payload, attempts, created_at) VALUES (:queue, :payload, 0, :now)",
            {"queue": job.queue, "payload": payload, "now": int(time.time())},
        )

    def pop(self, queue: str = "default") -> dict | None:
        from hunt.database.connection import raw
        result = raw(
            "SELECT * FROM jobs WHERE queue = :queue AND reserved_at IS NULL ORDER BY id LIMIT 1",
            {"queue": queue},
        )
        row = result.fetchone()
        if row is None:
            return None
        raw(
            "UPDATE jobs SET reserved_at = :now, attempts = attempts + 1 WHERE id = :id",
            {"now": int(time.time()), "id": row.id},
        )
        return dict(row._mapping)

    def delete(self, job_id: int) -> None:
        from hunt.database.connection import raw
        raw("DELETE FROM jobs WHERE id = :id", {"id": job_id})

    def release(self, job_id: int, delay: int = 0) -> None:
        from hunt.database.connection import raw
        raw(
            "UPDATE jobs SET reserved_at = NULL, available_at = :at WHERE id = :id",
            {"at": int(time.time()) + delay, "id": job_id},
        )

    def size(self, queue: str = "default") -> int:
        from hunt.database.connection import raw
        result = raw(
            "SELECT COUNT(*) as cnt FROM jobs WHERE queue = :queue AND reserved_at IS NULL",
            {"queue": queue},
        )
        row = result.fetchone()
        return row.cnt if row else 0
