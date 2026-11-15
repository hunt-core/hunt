from __future__ import annotations

import os
import time
import uuid as _uuid_mod

from hunt.queue.job import Job
from hunt.queue.serialization import keep_processed_for, processed_retention
from hunt.queue.serialization import make_payload as _make_payload
from hunt.queue.serialization import serialize_job as _serialize_job

# Module-level so tests can patch hunt.queue.drivers.database.raw
try:
    from hunt.database.connection import begin, raw
except Exception:  # pragma: no cover
    raw = None  # type: ignore[assignment]
    begin = None  # type: ignore[assignment]

DEFAULT_RETRY_AFTER = 90

# How many candidates pop() tries to claim before reporting an empty queue.
_MAX_CLAIM_ATTEMPTS = 25

# Finished job ids (and the leases of running ones) live here.
PROCESSED_TABLE = "jobs_processed"


def _now() -> int:
    return int(time.time())


def _serialize_chain(jobs: list[Job]) -> list[dict]:
    return [_serialize_job(j) for j in jobs]


def retry_after_from_env(default: int = DEFAULT_RETRY_AFTER) -> int:
    try:
        return max(1, int(os.environ.get("QUEUE_RETRY_AFTER", default)))
    except (TypeError, ValueError):
        return default


def _atomic(statements: list[tuple[str, dict]]) -> None:
    """Run several statements in one transaction on the default connection."""
    from sqlalchemy import text

    with begin() as conn:
        for sql, bindings in statements:
            conn.execute(text(sql), bindings)


def _is_unique_violation(exc: BaseException) -> bool:
    try:
        from sqlalchemy.exc import IntegrityError
    except Exception:  # pragma: no cover
        return False
    return isinstance(exc, IntegrityError)


def _mark_finished(conn, job_uid: str, stamp: int) -> bool:
    """Record *job_uid* as finished inside the caller's transaction.

    Returns True when this call recorded it and False when it was already
    recorded. Two transactions racing to insert the same id cannot both
    commit: the unique index on ``job_id`` rejects the second.
    """
    from sqlalchemy import text

    updated = conn.execute(
        text(
            "UPDATE jobs_processed SET processed_at = :at, reserved_until = NULL"
            " WHERE job_id = :uid AND processed_at IS NULL"
        ),
        {"at": stamp, "uid": job_uid},
    )
    if updated.rowcount == 1:
        return True
    existing = conn.execute(
        text("SELECT processed_at FROM jobs_processed WHERE job_id = :uid"), {"uid": job_uid}
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        text("INSERT INTO jobs_processed (job_id, processed_at, reserved_until) VALUES (:uid, :at, NULL)"),
        {"at": stamp, "uid": job_uid},
    )
    return True


class DatabaseDriver:
    """Stores jobs in a `jobs` table. Run `hunt queue:work` to process.

    ``retry_after`` (seconds, default 90, env ``QUEUE_RETRY_AFTER``) is how long
    a job may stay reserved before another worker may take it over. It must be
    longer than the longest job timeout, or a slow job will be run twice.

    Replay protection uses the ``jobs_processed`` table, one row per job id:

    * ``processed_at IS NULL``: a worker is running the job and holds it until
      ``reserved_until`` (the same moment its reservation of the ``jobs`` row
      expires). A second delivery of the id is postponed, not run.
    * ``processed_at`` set: the job finished (completed, or failed for good)
      and is never run again. The row is written in the same transaction that
      removes the job from ``jobs``.

    ``processed_retention`` (seconds, default 7 days, env
    ``QUEUE_PROCESSED_RETENTION``) is how long finished ids are kept.
    """

    def __init__(self, retry_after: int | None = None, processed_retention: int | None = None) -> None:
        self.retry_after = max(1, int(retry_after)) if retry_after is not None else retry_after_from_env()
        self.processed_retention = processed_retention
        # job id -> the reserved_until value this process wrote for it.
        self._leases: dict[str, int] = {}

    def push(self, job: Job) -> None:
        payload = _make_payload(_serialize_job(job))
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, NULL, :now)",
            {"queue": job.queue, "payload": payload, "now": _now()},
        )

    def later(self, delay: int, job: Job) -> None:
        """Enqueue a job to be processed after `delay` seconds."""
        now = _now()
        body = _serialize_job(job)
        # Signed, so the payload's age is measured from when it becomes due.
        body["available_at"] = now + delay
        payload = _make_payload(body)
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, :at, :now)",
            {"queue": job.queue, "payload": payload, "at": now + delay, "now": now},
        )

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        """Re-enqueue a pre-serialized body dict (used for chain continuation)."""
        payload = _make_payload(body_dict)
        raw(
            "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
            " VALUES (:queue, :payload, 0, NULL, :now)",
            {"queue": queue, "payload": payload, "now": _now()},
        )

    def pop(self, queue: str = "default") -> dict | None:
        """Reserve and return the next ready job, or None.

        A job is ready when it is unreserved, or when its reservation is older
        than ``retry_after`` (its worker died mid-job).
        """
        for _ in range(_MAX_CLAIM_ATTEMPTS):
            now = _now()
            # Step 1: find the best candidate without locking.
            candidate = raw(
                "SELECT id, reserved_at FROM jobs"
                " WHERE queue = :queue AND (reserved_at IS NULL OR reserved_at <= :expired)"
                " AND (available_at IS NULL OR available_at <= :now)"
                " ORDER BY id LIMIT 1",
                {"queue": queue, "now": now, "expired": now - self.retry_after},
            ).fetchone()
            if candidate is None:
                return None
            # Step 2: CAS claim. It only succeeds if the row still has the
            # reservation state we saw, so of several workers racing for the
            # same row (fresh or stale) exactly one UPDATE matches.
            seen = candidate.reserved_at
            if seen is None:
                claim = raw(
                    "UPDATE jobs SET reserved_at = :now, attempts = attempts + 1"
                    " WHERE id = :id AND reserved_at IS NULL",
                    {"id": candidate.id, "now": now},
                )
            else:
                claim = raw(
                    "UPDATE jobs SET reserved_at = :now, attempts = attempts + 1"
                    " WHERE id = :id AND reserved_at = :seen",
                    {"id": candidate.id, "now": now, "seen": seen},
                )
            if claim.rowcount == 0:
                continue  # lost the race; other jobs may still be ready
            row = raw("SELECT * FROM jobs WHERE id = :id", {"id": candidate.id}).fetchone()
            if row is None:
                continue
            job_data = dict(row._mapping)
            job_data["reserved_until"] = now + self.retry_after
            return job_data
        return None

    def delete(self, job_id: int) -> None:
        raw("DELETE FROM jobs WHERE id = :id", {"id": job_id})

    def release(self, job_id: int, delay: int = 0) -> None:
        raw(
            "UPDATE jobs SET reserved_at = NULL, available_at = :at WHERE id = :id",
            {"at": _now() + delay, "id": job_id},
        )

    def defer(self, job_id: int, delay: int = 1) -> None:
        """Put a job back without it counting as an attempt."""
        raw(
            "UPDATE jobs SET reserved_at = NULL, available_at = :at,"
            " attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END WHERE id = :id",
            {"at": _now() + delay, "id": job_id},
        )

    def fail(
        self,
        job_id: int,
        queue: str,
        payload: str,
        exception: str,
        job_uid: str | None = None,
        keep_from: float | None = None,
    ) -> None:
        """Move a job to the jobs_failed table (one transaction).

        With *job_uid* (the verified id from the payload) the id is recorded
        as finished in the same transaction, so a copy of the failed payload
        cannot be put back on the queue and run.
        """
        from sqlalchemy import text

        record = {
            "uuid": str(_uuid_mod.uuid4()),
            "conn": "database",
            "queue": queue,
            "payload": payload,
            "exc": exception,
            "at": _now(),
        }
        if job_uid is None:
            _atomic(
                [
                    (
                        "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
                        " VALUES (:uuid, :conn, :queue, :payload, :exc, :at)",
                        record,
                    ),
                    ("DELETE FROM jobs WHERE id = :id", {"id": job_id}),
                ]
            )
            return
        stamp = self._finished_stamp(keep_from)
        for attempt in (1, 2):
            try:
                with begin() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
                            " VALUES (:uuid, :conn, :queue, :payload, :exc, :at)"
                        ),
                        record,
                    )
                    _mark_finished(conn, job_uid, stamp)
                    conn.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
                break
            except Exception as exc:
                # Another worker recorded the id between our check and insert:
                # run the transaction again, which now sees that record.
                if attempt == 2 or not _is_unique_violation(exc):
                    raise
        self._leases.pop(job_uid, None)

    # ------------------------------------------------------------------
    # Replay protection (jobs_processed)
    # ------------------------------------------------------------------

    def processed_store_ready(self) -> bool:
        """True when the ``jobs_processed`` table exists with the expected columns."""
        try:
            raw("SELECT job_id, processed_at, reserved_until FROM jobs_processed WHERE 1 = 0")
        except Exception:
            return False
        return True

    def processed_store_hint(self) -> str:
        return (
            "the `jobs_processed` table is missing (or the database is unreachable). "
            "Run `hunt queue:table --processed-only` and `hunt migrate` to create it"
        )

    def _finished_stamp(self, keep_from: float | None) -> int:
        # A job that ran before it was due (its row was moved forward by hand)
        # is remembered from its due time, so that its id is not forgotten
        # while the payload is still young enough to be accepted.
        return max(_now(), int(keep_from or 0))

    def claim_run(self, job_uid: str, job_data: dict | None = None) -> str:
        """Take the right to run *job_uid*.

        Returns ``"claimed"``, ``"done"`` (the id already finished: drop this
        delivery) or ``"busy"`` (another delivery of the id is running right
        now: postpone this one). A lease left by a dead worker expires
        together with its reservation, so the redelivered job is claimable.
        """
        for _ in range(3):
            now = _now()
            until = max(int((job_data or {}).get("reserved_until") or 0), now + 1)
            try:
                raw(
                    "INSERT INTO jobs_processed (job_id, processed_at, reserved_until) VALUES (:uid, NULL, :until)",
                    {"uid": job_uid, "until": until},
                )
                self._leases[job_uid] = until
                return "claimed"
            except Exception as exc:
                if not _is_unique_violation(exc):
                    raise
            row = raw(
                "SELECT processed_at, reserved_until FROM jobs_processed WHERE job_id = :uid", {"uid": job_uid}
            ).fetchone()
            if row is None:
                continue  # pruned or released in between: insert again
            if row.processed_at is not None:
                return "done"
            seen = row.reserved_until
            if seen is not None and seen > now:
                return "busy"
            # Expired lease: compare-and-swap so only one worker takes it over.
            if seen is None:
                taken = raw(
                    "UPDATE jobs_processed SET reserved_until = :until"
                    " WHERE job_id = :uid AND processed_at IS NULL AND reserved_until IS NULL",
                    {"uid": job_uid, "until": until},
                )
            else:
                taken = raw(
                    "UPDATE jobs_processed SET reserved_until = :until"
                    " WHERE job_id = :uid AND processed_at IS NULL AND reserved_until = :seen",
                    {"uid": job_uid, "until": until, "seen": seen},
                )
            if taken.rowcount == 1:
                self._leases[job_uid] = until
                return "claimed"
        return "busy"

    def release_run(self, job_uid: str) -> None:
        """Give up the lease taken by :meth:`claim_run` (the job will be retried)."""
        until = self._leases.pop(job_uid, None)
        if until is None:
            return
        raw(
            "DELETE FROM jobs_processed WHERE job_id = :uid AND processed_at IS NULL AND reserved_until = :until",
            {"uid": job_uid, "until": until},
        )

    def complete(self, job_id: int, job_uid: str, keep_from: float | None = None) -> bool:
        """Remove a finished job and record its id, in one transaction.

        Returns False when the id was already recorded as finished by another
        worker (the job outlived ``retry_after`` and ran twice); the row is
        removed either way.
        """
        from sqlalchemy import text

        stamp = self._finished_stamp(keep_from)
        won = False
        for attempt in (1, 2):
            try:
                with begin() as conn:
                    won = _mark_finished(conn, job_uid, stamp)
                    conn.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})
                break
            except Exception as exc:
                if attempt == 2 or not _is_unique_violation(exc):
                    raise
        self._leases.pop(job_uid, None)
        return won

    def prune_processed(self) -> int:
        """Forget ids finished longer ago than the retention window. Returns the number removed."""
        keep = keep_processed_for(self.processed_retention)
        if keep == 0:
            return 0
        cutoff = _now() - keep
        removed = raw(
            "DELETE FROM jobs_processed WHERE (processed_at IS NOT NULL AND processed_at < :cutoff)"
            " OR (processed_at IS NULL AND (reserved_until IS NULL OR reserved_until < :cutoff))",
            {"cutoff": cutoff},
        )
        return max(0, int(removed.rowcount or 0))

    def processed_retention_seconds(self) -> int:
        return processed_retention(self.processed_retention)

    def retry_failed(self, failed_id: int, body_dict: dict, queue: str = "default") -> bool:
        """Move a failed job back onto the queue (one transaction).

        Returns False, and enqueues nothing, when the failed row is already
        gone (retried or deleted by someone else), so two concurrent retries
        cannot enqueue the job twice.
        """
        from sqlalchemy import text

        with begin() as conn:
            deleted = conn.execute(text("DELETE FROM jobs_failed WHERE id = :id"), {"id": failed_id})
            if deleted.rowcount == 0:
                return False
            conn.execute(
                text(
                    "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
                    " VALUES (:queue, :payload, 0, NULL, :now)"
                ),
                {"queue": queue, "payload": _make_payload(body_dict), "now": _now()},
            )
        return True

    def size(self, queue: str = "default") -> int:
        """Number of jobs that are ready to run now (including reclaimable ones)."""
        now = _now()
        result = raw(
            "SELECT COUNT(*) as cnt FROM jobs"
            " WHERE queue = :queue AND (reserved_at IS NULL OR reserved_at <= :expired)"
            " AND (available_at IS NULL OR available_at <= :now)",
            {"queue": queue, "now": now, "expired": now - self.retry_after},
        )
        row = result.fetchone()
        return row.cnt if row else 0
