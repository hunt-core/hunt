from __future__ import annotations

from typing import Any

from hunt.queue.job import Job


class _QueueManager:
    def __init__(self) -> None:
        self._driver: Any = None

    def configure(self, driver: str = "sync") -> None:
        if driver == "database":
            from hunt.queue.drivers.database import DatabaseDriver
            self._driver = DatabaseDriver()
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

    def size(self, queue: str = "default") -> int:
        return self._get_driver().size(queue)


Queue = _QueueManager()
