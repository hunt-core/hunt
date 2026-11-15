from __future__ import annotations

from hunt.queue.job import Job
from hunt.queue.serialization import round_trip
from hunt.queue.worker import JobTimeout, call_handler, run_with_timeout


class JobTimeoutError(Exception):
    """Raised when a job exceeds its configured timeout."""


class SyncDriver:
    """Runs jobs immediately in the calling process (no real queue).

    The job is first serialized to JSON and rebuilt, exactly as a queue worker
    would do, and it is the rebuilt copy that runs. A job that cannot survive
    that round-trip therefore fails here, in development, instead of only
    failing once a real driver is configured.
    """

    def push(self, job: Job) -> None:
        instance = round_trip(job)
        chain = list(getattr(job, "_chain", []))
        timeout = int(getattr(instance, "timeout", 60) or 0)
        try:
            # The alarm-based timeout only exists on the main thread; elsewhere
            # (e.g. OFFLOAD_SYNC_HANDLERS worker threads) the job runs untimed.
            run_with_timeout(lambda: call_handler(instance), timeout, thread_fallback=False)
        except JobTimeout:
            raise JobTimeoutError(f"{type(job).__name__} exceeded its {timeout}s timeout.") from None
        for chained in chain:
            self.push(chained)

    def later(self, delay: int, job: Job) -> None:
        """Sync driver ignores delay and runs immediately."""
        self.push(job)

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        pass

    def pop(self, queue: str = "default") -> Job | None:
        return None

    def delete(self, job_id: int) -> None:
        pass

    def release(self, job_id: int, delay: int = 0) -> None:
        pass

    def fail(self, job_id: int, queue: str, payload: str, exception: str) -> None:
        pass

    def size(self, queue: str = "default") -> int:
        return 0

    def _dispatch_chain(self, job: Job) -> None:
        for chained in getattr(job, "_chain", []):
            self.push(chained)
