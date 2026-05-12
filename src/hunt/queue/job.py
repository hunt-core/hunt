from __future__ import annotations

from typing import Any


class Job:
    """Base class for queueable jobs."""

    queue: str = "default"
    tries: int = 3
    timeout: int = 60

    def handle(self) -> None:
        raise NotImplementedError

    def failed(self, exc: Exception) -> None:
        """Called when the job exhausts all retry attempts."""

    def dispatch(self) -> None:
        from hunt.queue.manager import Queue
        Queue.push(self)

    @classmethod
    def dispatch_now(cls, **kwargs: Any) -> None:
        instance = cls(**kwargs)
        instance.handle()
