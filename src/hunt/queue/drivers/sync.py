from __future__ import annotations

from hunt.queue.job import Job


class SyncDriver:
    """Runs jobs immediately in the calling process (no real queue)."""

    def push(self, job: Job) -> None:
        job.handle()

    def pop(self, queue: str = "default") -> Job | None:
        return None

    def size(self, queue: str = "default") -> int:
        return 0
