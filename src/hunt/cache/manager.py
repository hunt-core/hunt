from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable


class ArrayStore:
    """In-memory cache (lost between requests in production; good for tests)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self._data:
            return default
        value, expires_at = self._data[key]
        if expires_at and expires_at < time.time():
            del self._data[key]
            return default
        return value

    def put(self, key: str, value: Any, seconds: int = 0) -> None:
        self._data[key] = (value, time.time() + seconds if seconds else 0)

    def forever(self, key: str, value: Any) -> None:
        self._data[key] = (value, 0)

    def forget(self, key: str) -> None:
        self._data.pop(key, None)

    def flush(self) -> None:
        self._data.clear()

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def increment(self, key: str, amount: int = 1) -> int:
        val = int(self.get(key, 0)) + amount
        self.put(key, val)
        return val

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)

    def remember(self, key: str, seconds: int, callback: Callable) -> Any:
        value = self.get(key)
        if value is None:
            value = callback()
            self.put(key, value, seconds)
        return value

    def remember_forever(self, key: str, callback: Callable) -> Any:
        value = self.get(key)
        if value is None:
            value = callback()
            self.forever(key, value)
        return value


class FileStore(ArrayStore):
    """File-backed cache. Survives restarts; not suitable for high concurrency."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, key: str) -> Path:
        import hashlib
        hashed = hashlib.md5(key.encode()).hexdigest()
        return self._path / hashed[:2] / hashed

    def get(self, key: str, default: Any = None) -> Any:
        f = self._cache_file(key)
        if not f.exists():
            return default
        try:
            payload = json.loads(f.read_text())
            if payload["expires_at"] and payload["expires_at"] < time.time():
                f.unlink(missing_ok=True)
                return default
            return payload["value"]
        except Exception:
            return default

    def put(self, key: str, value: Any, seconds: int = 0) -> None:
        f = self._cache_file(key)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({
            "value": value,
            "expires_at": time.time() + seconds if seconds else 0,
        }))

    def forever(self, key: str, value: Any) -> None:
        self.put(key, value, 0)

    def forget(self, key: str) -> None:
        self._cache_file(key).unlink(missing_ok=True)

    def flush(self) -> None:
        import shutil
        shutil.rmtree(self._path, ignore_errors=True)
        self._path.mkdir(parents=True, exist_ok=True)

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def increment(self, key: str, amount: int = 1) -> int:
        val = int(self.get(key, 0)) + amount
        self.put(key, val)
        return val

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)


class _CacheManager:
    _store: ArrayStore | FileStore | None = None

    def configure(self, driver: str = "file", path: Path | None = None) -> None:
        if driver == "array":
            self._store = ArrayStore()
        else:
            self._store = FileStore(path or Path("storage/framework/cache"))

    def _get_store(self) -> ArrayStore:
        if self._store is None:
            self._store = ArrayStore()
        return self._store

    def get(self, key: str, default: Any = None) -> Any:
        return self._get_store().get(key, default)

    def put(self, key: str, value: Any, seconds: int = 0) -> None:
        self._get_store().put(key, value, seconds)

    def forever(self, key: str, value: Any) -> None:
        self._get_store().forever(key, value)

    def forget(self, key: str) -> None:
        self._get_store().forget(key)

    def flush(self) -> None:
        self._get_store().flush()

    def has(self, key: str) -> bool:
        return self._get_store().has(key)

    def increment(self, key: str, amount: int = 1) -> int:
        return self._get_store().increment(key, amount)

    def decrement(self, key: str, amount: int = 1) -> int:
        return self._get_store().decrement(key, amount)

    def remember(self, key: str, seconds: int, callback: Callable) -> Any:
        return self._get_store().remember(key, seconds, callback)

    def remember_forever(self, key: str, callback: Callable) -> Any:
        return self._get_store().remember_forever(key, callback)


Cache = _CacheManager()
