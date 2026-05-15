from __future__ import annotations

from typing import Any

from hunt.queue.job import Job


class _QueueManager:
    def __init__(self) -> None:
        self._driver: Any = None

    def configure(self, driver: str = "sync", **config: Any) -> None:
        if driver == "database":
            from hunt.queue.drivers.database import DatabaseDriver

            self._driver = DatabaseDriver()
        elif driver == "redis":
            from hunt.queue.drivers.redis import RedisDriver

            self._driver = RedisDriver(**config)
        else:
            from hunt.queue.drivers.sync import SyncDriver

            self._driver = SyncDriver()

    def _get_driver(self) -> Any:
        if self._driver is None:
            from hunt.queue.drivers.sync import SyncDriver

            self._driver = SyncDriver()
        return self._driver

    def push(self, job: Job) -> None:
        self._get_driver().push(job)

    def later(self, delay: int, job: Job) -> None:
        """Dispatch a job after `delay` seconds."""
        self._get_driver().later(delay, job)

    def size(self, queue: str = "default") -> int:
        return self._get_driver().size(queue)


Queue = _QueueManager()
