from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Redis store
# ---------------------------------------------------------------------------


class RedisStore:
    """Cache store backed by Redis.

    Install redis-py to use: ``pip install redis``
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "hunt_cache:",
    ) -> None:
        try:
            import redis as _redis
        except ImportError as exc:
            raise ImportError("Install redis-py to use the Redis cache driver: pip install redis") from exc
        self._redis = _redis.Redis(host=host, port=port, db=db, password=password, decode_responses=False)
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str, default: Any = None) -> Any:
        raw = self._redis.get(self._key(key))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    def put(self, key: str, value: Any, seconds: int = 0) -> None:
        encoded = json.dumps(value, default=str).encode()
        if seconds:
            self._redis.setex(self._key(key), seconds, encoded)
        else:
            self._redis.set(self._key(key), encoded)

    def forever(self, key: str, value: Any) -> None:
        self.put(key, value, 0)

    def forget(self, key: str) -> None:
        self._redis.delete(self._key(key))

    def flush(self) -> None:
        pattern = f"{self._prefix}*"
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
            if keys:
                self._redis.delete(*keys)
            if cursor == 0:
                break

    def has(self, key: str) -> bool:
        return bool(self._redis.exists(self._key(key)))

    def increment(self, key: str, amount: int = 1) -> int:
        return int(self._redis.incrby(self._key(key), amount))

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)

    def add(self, key: str, value: Any, seconds: int = 0) -> bool:
        encoded = json.dumps(value, default=str).encode()
        if seconds:
            result = self._redis.set(self._key(key), encoded, ex=seconds, nx=True)
        else:
            result = self._redis.setnx(self._key(key), encoded)
        return bool(result)

    def pull(self, key: str, default: Any = None) -> Any:
        pipe = self._redis.pipeline()
        k = self._key(key)
        pipe.get(k)
        pipe.delete(k)
        raw, _ = pipe.execute()
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            return {}
        rkeys = [self._key(k) for k in keys]
        values = self._redis.mget(rkeys)
        result: dict[str, Any] = {}
        for k, raw in zip(keys, values, strict=False):
            result[k] = json.loads(raw) if raw is not None else None
        return result

    def put_many(self, values: dict[str, Any], seconds: int = 0) -> None:
        pipe = self._redis.pipeline()
        for k, v in values.items():
            encoded = json.dumps(v, default=str).encode()
            if seconds:
                pipe.setex(self._key(k), seconds, encoded)
            else:
                pipe.set(self._key(k), encoded)
        pipe.execute()

    def remember(self, key: str, seconds: int, callback: Callable) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        value = callback()
        self.add(key, value, seconds)
        return value

    def remember_forever(self, key: str, callback: Callable) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        value = callback()
        self.add(key, value)
        return value


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

    def add(self, key: str, value: Any, seconds: int = 0) -> bool:
        """Store value only if the key is not already present. Returns True if stored."""
        if self.has(key):
            return False
        self.put(key, value, seconds)
        return True

    def pull(self, key: str, default: Any = None) -> Any:
        """Get and remove a cache entry in one call."""
        value = self.get(key, default)
        self.forget(key)
        return value

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        return {k: self.get(k) for k in keys}

    def put_many(self, values: dict[str, Any], seconds: int = 0) -> None:
        for k, v in values.items():
            self.put(k, v, seconds)

    def increment(self, key: str, amount: int = 1) -> int:
        val = int(self.get(key, 0)) + amount
        self.put(key, val)
        return val

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)

    def remember(self, key: str, seconds: int, callback: Callable) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        value = callback()
        self.add(key, value, seconds)
        return value

    def remember_forever(self, key: str, callback: Callable) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        value = callback()
        self.add(key, value)
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
        f.write_text(
            json.dumps(
                {
                    "value": value,
                    "expires_at": time.time() + seconds if seconds else 0,
                }
            )
        )

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

    def add(self, key: str, value: Any, seconds: int = 0) -> bool:
        if self.has(key):
            return False
        self.put(key, value, seconds)
        return True

    def pull(self, key: str, default: Any = None) -> Any:
        value = self.get(key, default)
        self.forget(key)
        return value

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        return {k: self.get(k) for k in keys}

    def put_many(self, values: dict[str, Any], seconds: int = 0) -> None:
        for k, v in values.items():
            self.put(k, v, seconds)

    def increment(self, key: str, amount: int = 1) -> int:
        val = int(self.get(key, 0)) + amount
        self.put(key, val)
        return val

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)


class _CacheManager:
    _store: ArrayStore | FileStore | RedisStore | None = None

    def configure(
        self,
        driver: str = "file",
        path: Path | None = None,
        host: str | None = None,
        port: int | None = None,
        db: int | None = None,
        password: str | None = None,
        prefix: str | None = None,
    ) -> None:
        """Configure the cache store. Redis connection settings not passed
        explicitly fall back to the REDIS_* / CACHE_PREFIX env vars."""
        if driver == "array":
            self._store = ArrayStore()
        elif driver == "redis":
            self._store = RedisStore(
                host=host or os.environ.get("REDIS_HOST", "127.0.0.1"),
                port=port if port is not None else int(os.environ.get("REDIS_PORT", "6379")),
                db=db if db is not None else int(os.environ.get("REDIS_DB", "0")),
                password=password if password is not None else (os.environ.get("REDIS_PASSWORD") or None),
                prefix=prefix or os.environ.get("CACHE_PREFIX", "hunt_cache:"),
            )
        else:
            self._store = FileStore(path or Path(os.environ.get("CACHE_PATH", "storage/framework/cache")))

    def _get_store(self) -> ArrayStore | FileStore | RedisStore:
        if self._store is None:
            # Unconfigured: honour CACHE_DRIVER, defaulting to the in-memory
            # store so tests and scripts never touch disk or a server.
            self.configure(os.environ.get("CACHE_DRIVER", "array"))
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

    def add(self, key: str, value: Any, seconds: int = 0) -> bool:
        return self._get_store().add(key, value, seconds)

    def pull(self, key: str, default: Any = None) -> Any:
        return self._get_store().pull(key, default)

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        return self._get_store().get_many(keys)

    def put_many(self, values: dict[str, Any], seconds: int = 0) -> None:
        self._get_store().put_many(values, seconds)

    def remember(self, key: str, seconds: int, callback: Callable) -> Any:
        return self._get_store().remember(key, seconds, callback)

    def remember_forever(self, key: str, callback: Callable) -> Any:
        return self._get_store().remember_forever(key, callback)


Cache = _CacheManager()
