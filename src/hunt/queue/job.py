from __future__ import annotations

from typing import Any


class Job:
    """Base class for queueable jobs."""

    queue: str = "default"
    tries: int = 3
    timeout: int = 60
    # backoff seconds between retries; can be a list for stepped backoff
    backoff: int | list[int] = 0

    def handle(self) -> None:
        raise NotImplementedError

    def failed(self, exc: Exception) -> None:
        """Called when the job exhausts all retry attempts."""

    def chain(self, jobs: list[Job]) -> Job:
        """Set jobs to dispatch sequentially after this one succeeds."""
        self._chain: list[Job] = list(jobs)
        return self

    def dispatch(self) -> None:
        from hunt.queue.manager import Queue

        Queue.push(self)

    def dispatch_later(self, delay: int) -> None:
        """Push the job to the queue with a delay in seconds."""
        from hunt.queue.manager import Queue

        Queue.later(delay, self)

    @classmethod
    def dispatch_now(cls, **kwargs: Any) -> None:
        instance = cls(**kwargs)
        instance.handle()
