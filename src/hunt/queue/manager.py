from __future__ import annotations

import os
from typing import Any

from hunt.queue.job import Job


def _redis_config_from_env() -> dict[str, Any]:
    """Build RedisDriver kwargs from REDIS_* environment variables."""
    config: dict[str, Any] = {
        "host": os.environ.get("REDIS_HOST", "127.0.0.1"),
        "port": int(os.environ.get("REDIS_PORT", "6379")),
        "db": int(os.environ.get("REDIS_DB", "0")),
    }
    password = os.environ.get("REDIS_PASSWORD")
    if password:
        config["password"] = password
    return config


def _build_driver(driver: str, **config: Any) -> Any:
    if driver == "database":
        from hunt.queue.drivers.database import DatabaseDriver

        return DatabaseDriver()
    elif driver == "redis":
        from hunt.queue.drivers.redis import RedisDriver

        return RedisDriver(**(config or _redis_config_from_env()))
    else:
        from hunt.queue.drivers.sync import SyncDriver

        return SyncDriver()


class _QueueManager:
    def __init__(self) -> None:
        self._driver: Any = None

    def configure(self, driver: str = "sync", **config: Any) -> None:
        self._driver = _build_driver(driver, **config)

    def _get_driver(self) -> Any:
        if self._driver is None:
            self._driver = _build_driver(os.environ.get("QUEUE_DRIVER", "sync"))
        return self._driver

    def push(self, job: Job) -> None:
        self._get_driver().push(job)

    def later(self, delay: int, job: Job) -> None:
        """Dispatch a job after `delay` seconds."""
        self._get_driver().later(delay, job)

    def size(self, queue: str = "default") -> int:
        return self._get_driver().size(queue)


Queue = _QueueManager()
