from __future__ import annotations

import json
import time
import uuid as _uuid_mod

from hunt.queue.job import Job

# Module-level so tests can patch hunt.queue.drivers.database.raw
try:
    from hunt.database.connection import raw
except Exception:  # pragma: no cover
    raw = None  # type: ignore[assignment]


def _serialize_job(job: Job) -> dict:
    """Build the body dict for a job, excluding private attrs."""
    data = {k: v for k, v in vars(job).items() if not k.startswith("_")}
    chain = _serialize_chain(getattr(job, "_chain", []))
    return {
        "class": f"{type(job).__module__}.{type(job).__name__}",
        "data": data,
        "chain": chain,
        "queue": job.queue,
        "tries": getattr(job, "tries", 3),
        "backoff": getattr(job, "backoff", 0),
    }


def _serialize_chain(jobs: list[Job]) -> list[dict]:
    return [_serialize_job(j) for j in jobs]


def _make_payload(body_dict: dict) -> str:
    from hunt.security.signing import sign

    body = json.dumps(body_dict, sort_keys=True)
    return json.dumps({"body": body, "signature": sign(body)})


class DatabaseDriver:
    """Stores jobs in a `jobs` table. Run `hunt queue:work` to process."""

    def push(self, job: Job) -> None:
        payload = _make_payload(_serialize_job(job))
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, NULL, :now)",
            {"queue": job.queue, "payload": payload, "now": int(time.time())},
        )

    def later(self, delay: int, job: Job) -> None:
        """Enqueue a job to be processed after `delay` seconds."""
        payload = _make_payload(_serialize_job(job))
        available_at = int(time.time()) + delay
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, :at, :now)",
            {"queue": job.queue, "payload": payload, "at": available_at, "now": int(time.time())},
        )

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        """Re-enqueue a pre-serialized body dict (used for chain continuation)."""
        payload = _make_payload(body_dict)
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, NULL, :now)",
            {"queue": queue, "payload": payload, "now": int(time.time())},
        )

    def pop(self, queue: str = "default") -> dict | None:
        now = int(time.time())
        # Step 1: find the best candidate without locking.
        candidate = raw(
            "SELECT id FROM jobs"
            " WHERE queue = :queue AND reserved_at IS NULL"
            " AND (available_at IS NULL OR available_at <= :now)"
            " ORDER BY id LIMIT 1",
            {"queue": queue, "now": now},
        ).fetchone()
        if candidate is None:
            return None
        # Step 2: CAS claim — only succeeds if the row is still unclaimed.
        # Two concurrent workers that both selected the same id will race here;
        # exactly one UPDATE will match (reserved_at IS NULL), the other gets
        # rowcount=0 and returns None without double-executing the job.
        claim = raw(
            "UPDATE jobs SET reserved_at = :now, attempts = attempts + 1 WHERE id = :id AND reserved_at IS NULL",
            {"id": candidate.id, "now": now},
        )
        if claim.rowcount == 0:
            return None
        row = raw("SELECT * FROM jobs WHERE id = :id", {"id": candidate.id}).fetchone()
        return dict(row._mapping) if row else None

    def delete(self, job_id: int) -> None:
        raw("DELETE FROM jobs WHERE id = :id", {"id": job_id})

    def release(self, job_id: int, delay: int = 0) -> None:
        raw(
            "UPDATE jobs SET reserved_at = NULL, available_at = :at WHERE id = :id",
            {"at": int(time.time()) + delay, "id": job_id},
        )

    def fail(self, job_id: int, queue: str, payload: str, exception: str) -> None:
        """Move a job to the jobs_failed table."""
        raw(
            "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
            " VALUES (:uuid, :conn, :queue, :payload, :exc, :at)",
            {
                "uuid": str(_uuid_mod.uuid4()),
                "conn": "database",
                "queue": queue,
                "payload": payload,
                "exc": exception,
                "at": int(time.time()),
            },
        )
        self.delete(job_id)

    def size(self, queue: str = "default") -> int:
        result = raw(
            "SELECT COUNT(*) as cnt FROM jobs WHERE queue = :queue AND reserved_at IS NULL"
            " AND (available_at IS NULL OR available_at <= :now)",
            {"queue": queue, "now": int(time.time())},
        )
        row = result.fetchone()
        return row.cnt if row else 0
