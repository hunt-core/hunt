from __future__ import annotations

import signal

from hunt.queue.job import Job


class JobTimeoutError(Exception):
    """Raised when a job exceeds its configured timeout."""


class SyncDriver:
    """Runs jobs immediately in the calling process (no real queue)."""

    def push(self, job: Job) -> None:
        timeout = getattr(job, "timeout", 60)
        if timeout and timeout > 0 and hasattr(signal, "SIGALRM"):

            def _handle_timeout(signum: int, frame: object) -> None:
                raise JobTimeoutError(f"{type(job).__name__} exceeded its {timeout}s timeout.")

            old_handler = signal.signal(signal.SIGALRM, _handle_timeout)
            signal.alarm(timeout)
            try:
                job.handle()
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        else:
            job.handle()
        self._dispatch_chain(job)

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
