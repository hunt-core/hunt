from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import threading
import time
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hunt.cache import codec

try:  # POSIX only; on other platforms file locks degrade to in-process locks.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

# Sentinel distinguishing "no entry" from a cached ``None``.
_MISSING: Any = object()

# Sentinel returned by ``_expires_at`` when the TTL says "do not store".
_EXPIRED = -1.0


def _expires_at(seconds: float | None) -> float:
    """Translate a TTL into an absolute expiry.

    ``None`` means "forever" (stored as ``0``).  A TTL that is zero or negative
    means the entry is already expired, so the caller must forget the key
    instead of storing it (signalled by ``_EXPIRED``).
    """
    if seconds is None:
        return 0
    if seconds <= 0:
        return _EXPIRED
    return time.time() + seconds


class _KeyLocks:
    """Striped per-key locks.

    Threads in this process are serialised by an ``RLock`` per stripe.  When a
    lock directory is given (file store) the stripe is additionally guarded by
    ``flock`` on a small lock file so other processes sharing the cache
    directory are serialised too.  Locks are re-entrant for the holding thread.
    """

    def __init__(self, lock_dir: Path | None = None, stripes: int = 64) -> None:
        self._dir = lock_dir if _fcntl is not None else None
        self._locks = [threading.RLock() for _ in range(stripes)]
        self._depth = [0] * stripes
        self._fds: list[int | None] = [None] * stripes

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        index = zlib.crc32(key.encode("utf-8", "surrogatepass")) % len(self._locks)
        lock = self._locks[index]
        lock.acquire()
        try:
            # _depth / _fds for a stripe are only touched while holding its RLock.
            if self._depth[index] == 0 and self._dir is not None:
                self._fds[index] = self._lock_file(index)
            self._depth[index] += 1
            try:
                yield
            finally:
                self._depth[index] -= 1
                fd = self._fds[index]
                if self._depth[index] == 0 and fd is not None:
                    self._fds[index] = None
                    try:
                        _fcntl.flock(fd, _fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
        finally:
            lock.release()

    def _lock_file(self, index: int) -> int:
        assert self._dir is not None
        self._dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._dir / f"{index:02x}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        except BaseException:
            os.close(fd)
            raise
        return fd


class _Store:
    """Behaviour shared by every cache store.

    Semantics are identical across drivers:

    * values round-trip through the tagged JSON codec in ``hunt.cache.codec``
      (datetime, date, time, Decimal, bytes and sets survive; tuples come back
      as lists; unsupported types raise ``TypeError``);
    * ``seconds=None`` stores forever, a TTL ``<= 0`` forgets the key;
    * ``has()`` and ``remember()`` treat a cached ``None`` as present;
    * ``add()`` and ``increment()`` are atomic, and ``increment()`` keeps the
      key's remaining TTL.
    """

    _locks: _KeyLocks

    def _lookup(self, key: str) -> Any:
        """Return the stored value, or ``_MISSING``."""
        raise NotImplementedError

    def put(self, key: str, value: Any, seconds: float | None = None) -> None:
        raise NotImplementedError

    def increment(self, key: str, amount: int = 1) -> int:
        raise NotImplementedError

    def get(self, key: str, default: Any = None) -> Any:
        value = self._lookup(key)
        return default if value is _MISSING else value

    def has(self, key: str) -> bool:
        return self._lookup(key) is not _MISSING

    def forever(self, key: str, value: Any) -> None:
        self.put(key, value, None)

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        return {k: self.get(k) for k in keys}

    def put_many(self, values: dict[str, Any], seconds: float | None = None) -> None:
        for k, v in values.items():
            self.put(k, v, seconds)

    def remember(self, key: str, seconds: float | None, callback: Callable) -> Any:
        """Return the cached value, computing and storing it on a miss.

        A per-key lock ensures concurrent callers in this process (and, for the
        file store, other processes on the same directory) compute the value
        once instead of stampeding.  A ``None`` result is cached like any other.
        """
        value = self._lookup(key)
        if value is not _MISSING:
            return value
        with self._locks.hold(key):
            value = self._lookup(key)
            if value is not _MISSING:
                return value
            value = callback()
            self.put(key, value, seconds)
            return value

    def remember_forever(self, key: str, callback: Callable) -> Any:
        return self.remember(key, None, callback)


# ---------------------------------------------------------------------------
# Redis store
# ---------------------------------------------------------------------------


class RedisStore(_Store):
    """Cache store backed by Redis.

    Install redis-py to use: ``pip install redis``.  Pass ``client`` to reuse an
    existing connection (``Cache.configure("redis")`` without explicit
    connection settings shares ``hunt.redis_connection.get_redis()``).

    ``remember()`` de-duplicates concurrent computation within one process
    only; Redis expires entries itself, so ``prune()`` is a no-op.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        prefix: str = "hunt_cache:",
        client: Any = None,
    ) -> None:
        if client is None:
            try:
                import redis as _redis
            except ImportError as exc:
                raise ImportError("Install redis-py to use the Redis cache driver: pip install redis") from exc
            client = _redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=False,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
        self._redis = client
        self._prefix = prefix
        self._locks = _KeyLocks()

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    @staticmethod
    def _decode(raw: Any) -> Any:
        if raw is None:
            return _MISSING
        try:
            return codec.loads(raw)
        except Exception:
            return _MISSING

    def _lookup(self, key: str) -> Any:
        return self._decode(self._redis.get(self._key(key)))

    def put(self, key: str, value: Any, seconds: float | None = None) -> None:
        encoded = codec.dumps(value)
        if seconds is None:
            self._redis.set(self._key(key), encoded)
        elif seconds <= 0:
            self._redis.delete(self._key(key))
        else:
            self._redis.set(self._key(key), encoded, ex=math.ceil(seconds))

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
        # INCRBY is atomic and leaves the key's TTL untouched.
        return int(self._redis.incrby(self._key(key), amount))

    def add(self, key: str, value: Any, seconds: float | None = None) -> bool:
        """Atomically store *value* only if the key is absent (``SET NX``)."""
        encoded = codec.dumps(value)
        if seconds is None:
            result = self._redis.set(self._key(key), encoded, nx=True)
        elif seconds <= 0:
            return False
        else:
            result = self._redis.set(self._key(key), encoded, ex=math.ceil(seconds), nx=True)
        return bool(result)

    def pull(self, key: str, default: Any = None) -> Any:
        pipe = self._redis.pipeline()
        k = self._key(key)
        pipe.get(k)
        pipe.delete(k)
        raw, _ = pipe.execute()
        value = self._decode(raw)
        return default if value is _MISSING else value

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            return {}
        values = self._redis.mget([self._key(k) for k in keys])
        result: dict[str, Any] = {}
        for k, raw in zip(keys, values, strict=False):
            value = self._decode(raw)
            result[k] = None if value is _MISSING else value
        return result

    def put_many(self, values: dict[str, Any], seconds: float | None = None) -> None:
        pipe = self._redis.pipeline()
        for k, v in values.items():
            encoded = codec.dumps(v)
            if seconds is None:
                pipe.set(self._key(k), encoded)
            elif seconds <= 0:
                pipe.delete(self._key(k))
            else:
                pipe.set(self._key(k), encoded, ex=math.ceil(seconds))
        pipe.execute()

    def keys(self) -> Iterator[str]:
        """Yield the original (un-prefixed) keys currently stored."""
        pattern = f"{self._prefix}*"
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(cursor, match=pattern, count=200)
            for raw in keys:
                k = raw.decode() if isinstance(raw, bytes | bytearray) else str(raw)
                yield k[len(self._prefix) :]
            if cursor == 0:
                break

    def prune(self) -> int:
        """No-op: Redis expires entries on its own."""
        return 0


# ---------------------------------------------------------------------------
# Array store
# ---------------------------------------------------------------------------


class ArrayStore(_Store):
    """In-memory cache (lost between requests in production; good for tests).

    Thread-safe.  Values are normalised through the shared codec on write and
    copied on read, so the store behaves like the file and Redis drivers:
    mutating a value after ``put`` or after ``get`` never changes the cache.
    """

    def __init__(self, prune_every: int = 1000) -> None:
        # key -> (value, expires_at); expires_at == 0 means "forever".
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.RLock()
        self._locks = _KeyLocks()
        self._prune_every = prune_every
        self._writes = 0

    def _entry(self, key: str) -> tuple[Any, float] | None:
        """Return the live ``(value, expires_at)`` entry, dropping it if expired."""
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry[1] and entry[1] < time.time():
            self._data.pop(key, None)
            return None
        return entry

    def _lookup(self, key: str) -> Any:
        with self._lock:
            entry = self._entry(key)
            return _MISSING if entry is None else copy.deepcopy(entry[0])

    def _set(self, key: str, value: Any, expires_at: float) -> None:
        self._data[key] = (value, expires_at)
        self._writes += 1
        if self._prune_every and self._writes % self._prune_every == 0:
            self.prune()

    def put(self, key: str, value: Any, seconds: float | None = None) -> None:
        normalised = codec.from_jsonable(codec.to_jsonable(value))
        expires_at = _expires_at(seconds)
        with self._lock:
            if expires_at == _EXPIRED:
                self._data.pop(key, None)
            else:
                self._set(key, normalised, expires_at)

    def forget(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def flush(self) -> None:
        with self._lock:
            self._data.clear()

    def add(self, key: str, value: Any, seconds: float | None = None) -> bool:
        """Atomically store value only if the key is not already present. Returns True if stored."""
        normalised = codec.from_jsonable(codec.to_jsonable(value))
        expires_at = _expires_at(seconds)
        with self._lock:
            if self._entry(key) is not None or expires_at == _EXPIRED:
                return False
            self._set(key, normalised, expires_at)
            return True

    def pull(self, key: str, default: Any = None) -> Any:
        """Get and remove a cache entry in one call."""
        with self._lock:
            entry = self._entry(key)
            self._data.pop(key, None)
            return default if entry is None else entry[0]

    def increment(self, key: str, amount: int = 1) -> int:
        """Atomically add *amount*; the key's remaining TTL is preserved."""
        with self._lock:
            entry = self._entry(key)
            current, expires_at = (0, 0) if entry is None else entry
            value = int(current or 0) + amount
            self._set(key, value, expires_at)
            return value

    def keys(self) -> Iterator[str]:
        """Yield the keys of all live entries."""
        now = time.time()
        with self._lock:
            live = [k for k, (_, exp) in self._data.items() if not exp or exp >= now]
        yield from live

    def prune(self) -> int:
        """Delete expired entries. Returns how many were removed."""
        now = time.time()
        with self._lock:
            dead = [k for k, (_, exp) in self._data.items() if exp and exp < now]
            for k in dead:
                del self._data[k]
        return len(dead)


# ---------------------------------------------------------------------------
# File store
# ---------------------------------------------------------------------------


class FileStore(ArrayStore):
    """File-backed cache. Survives restarts and is safe for concurrent writers.

    Each entry is one JSON file ``{"key", "value", "expires_at"}`` written to a
    unique temp file and renamed into place, so readers never see a partial
    file.  ``add``, ``increment``, ``pull`` and ``remember`` take a per-key lock
    (``flock`` on POSIX, so it also holds across processes; in-process only on
    platforms without ``fcntl``).  The original key is recorded in the payload
    and exposed through ``keys()``.
    """

    _LOCK_DIR = ".locks"

    def __init__(self, path: Path, prune_every: int = 1000) -> None:
        super().__init__(prune_every=prune_every)
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._locks = _KeyLocks(self._path / self._LOCK_DIR)

    def _cache_file(self, key: str) -> Path:
        # Not a security use: the digest only spreads keys over file names.
        hashed = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
        return self._path / hashed[:2] / hashed

    # -- low-level file access ------------------------------------------------

    @staticmethod
    def _read_payload(f: Path) -> dict | None:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or "value" not in payload:
            return None
        return payload

    @staticmethod
    def _is_expired(payload: dict, now: float | None = None) -> bool:
        expires_at = payload.get("expires_at") or 0
        try:
            return bool(expires_at) and float(expires_at) < (time.time() if now is None else now)
        except (TypeError, ValueError):
            return True

    def _live_payload(self, key: str) -> dict | None:
        """Return the unexpired payload stored for *key*, if any."""
        payload = self._read_payload(self._cache_file(key))
        if payload is None or self._is_expired(payload):
            return None
        if payload.get("key", key) != key:  # digest collision
            return None
        return payload

    def _serialise(self, key: str, jsonable: Any, expires_at: float) -> str:
        return json.dumps({"key": key, "value": jsonable, "expires_at": expires_at})

    def _write_temp(self, f: Path, data: str) -> str:
        """Write *data* to a unique temp file next to *f* and return its path."""
        for attempt in (1, 2):
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f"{f.name}.", suffix=".tmp")
                break
            except FileNotFoundError:
                # The directory was removed by a concurrent flush; retry once.
                if attempt == 2:
                    raise
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return tmp

    def _write(self, key: str, jsonable: Any, expires_at: float) -> None:
        f = self._cache_file(key)
        tmp = self._write_temp(f, self._serialise(key, jsonable, expires_at))
        try:
            # Write-then-rename so concurrent readers never see a half-written file.
            os.replace(tmp, f)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._count_write()

    def _count_write(self) -> None:
        with self._lock:
            self._writes += 1
            due = bool(self._prune_every) and self._writes % self._prune_every == 0
        if due:
            try:
                self.prune()
            except OSError:
                pass

    # -- store API ------------------------------------------------------------

    def _lookup(self, key: str) -> Any:
        f = self._cache_file(key)
        payload = self._read_payload(f)
        if payload is None:
            return _MISSING
        if self._is_expired(payload):
            # Re-check under the key lock so a fresh value written meanwhile
            # is not deleted by mistake.
            with self._locks.hold(key):
                current = self._read_payload(f)
                if current is not None and self._is_expired(current):
                    f.unlink(missing_ok=True)
            return _MISSING
        if payload.get("key", key) != key:
            return _MISSING
        try:
            return codec.from_jsonable(payload["value"])
        except Exception:
            return _MISSING

    def put(self, key: str, value: Any, seconds: float | None = None) -> None:
        jsonable = codec.to_jsonable(value)
        expires_at = _expires_at(seconds)
        if expires_at == _EXPIRED:
            self.forget(key)
            return
        self._write(key, jsonable, expires_at)

    def forget(self, key: str) -> None:
        self._cache_file(key).unlink(missing_ok=True)

    def flush(self) -> None:
        import shutil

        # Lock files are kept: removing one while it is held would let two
        # processes "own" the same stripe.
        for child in list(self._path.iterdir()) if self._path.exists() else []:
            if child.name == self._LOCK_DIR:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        self._path.mkdir(parents=True, exist_ok=True)

    def add(self, key: str, value: Any, seconds: float | None = None) -> bool:
        """Atomically create the entry only if the key is absent."""
        jsonable = codec.to_jsonable(value)
        expires_at = _expires_at(seconds)
        if expires_at == _EXPIRED:
            return False
        f = self._cache_file(key)
        with self._locks.hold(key):
            if self._live_payload(key) is not None:
                return False
            # Drop an expired or corrupt leftover, then create exclusively.
            f.unlink(missing_ok=True)
            tmp = self._write_temp(f, self._serialise(key, jsonable, expires_at))
            try:
                try:
                    # link() fails if the target exists: an atomic create-if-absent
                    # that also holds against writers not using our lock.
                    os.link(tmp, f)
                except FileExistsError:
                    return False
                except OSError:
                    # Filesystem without hard links: exclusive create instead.
                    try:
                        with open(f, "x", encoding="utf-8") as fh:
                            fh.write(self._serialise(key, jsonable, expires_at))
                    except FileExistsError:
                        return False
            finally:
                Path(tmp).unlink(missing_ok=True)
        self._count_write()
        return True

    def pull(self, key: str, default: Any = None) -> Any:
        with self._locks.hold(key):
            value = self._lookup(key)
            self.forget(key)
        return default if value is _MISSING else value

    def increment(self, key: str, amount: int = 1) -> int:
        """Atomically add *amount*; the key's remaining TTL is preserved."""
        with self._locks.hold(key):
            payload = self._live_payload(key)
            if payload is None:
                current, expires_at = 0, 0
            else:
                current, expires_at = payload["value"], payload.get("expires_at") or 0
            value = int(current or 0) + amount
            self._write(key, value, expires_at)
            return value

    def _entry_files(self) -> Iterator[Path]:
        try:
            buckets = [d for d in self._path.iterdir() if d.is_dir() and d.name != self._LOCK_DIR]
        except OSError:
            return
        for bucket in buckets:
            try:
                files = list(bucket.iterdir())
            except OSError:
                continue
            yield from (f for f in files if f.is_file())

    def keys(self) -> Iterator[str]:
        """Yield the original keys of all live entries.

        Entries written by versions that did not record the key are skipped.
        """
        now = time.time()
        for f in self._entry_files():
            if "." in f.name:  # temp file
                continue
            payload = self._read_payload(f)
            if payload is None or self._is_expired(payload, now):
                continue
            key = payload.get("key")
            if isinstance(key, str):
                yield key

    def prune(self) -> int:
        """Delete expired entries and stale temp files. Returns how many entries were removed."""
        removed = 0
        now = time.time()
        for f in self._entry_files():
            if f.name.endswith(".tmp"):
                try:
                    if f.stat().st_mtime < now - 3600:
                        f.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            payload = self._read_payload(f)
            if payload is None or not self._is_expired(payload, now):
                continue
            key = payload.get("key")
            with self._locks.hold(key if isinstance(key, str) else f.name):
                current = self._read_payload(f)
                if current is not None and self._is_expired(current):
                    f.unlink(missing_ok=True)
                    removed += 1
        return removed


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
        """Configure the cache store.

        With the Redis driver and no explicit connection settings the shared
        client from ``hunt.redis_connection.get_redis()`` is used (REDIS_URL /
        REDIS_* env vars, timeouts).  Passing host, port, db or password builds
        a dedicated client; unspecified settings fall back to the REDIS_* env
        vars.  The key prefix falls back to CACHE_PREFIX.
        """
        if driver == "array":
            self._store = ArrayStore()
        elif driver == "redis":
            prefix = prefix or os.environ.get("CACHE_PREFIX", "hunt_cache:")
            if host is None and port is None and db is None and password is None:
                from hunt.redis_connection import get_redis

                self._store = RedisStore(prefix=prefix, client=get_redis())
            else:
                self._store = RedisStore(
                    host=host or os.environ.get("REDIS_HOST", "127.0.0.1"),
                    port=port if port is not None else int(os.environ.get("REDIS_PORT", "6379")),
                    db=db if db is not None else int(os.environ.get("REDIS_DB", "0")),
                    password=password if password is not None else (os.environ.get("REDIS_PASSWORD") or None),
                    prefix=prefix,
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

    def put(self, key: str, value: Any, seconds: float | None = None) -> None:
        """Store a value. ``seconds=None`` keeps it forever; a TTL ``<= 0`` forgets the key."""
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

    def add(self, key: str, value: Any, seconds: float | None = None) -> bool:
        return self._get_store().add(key, value, seconds)

    def pull(self, key: str, default: Any = None) -> Any:
        return self._get_store().pull(key, default)

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        return self._get_store().get_many(keys)

    def put_many(self, values: dict[str, Any], seconds: float | None = None) -> None:
        self._get_store().put_many(values, seconds)

    def remember(self, key: str, seconds: float | None, callback: Callable) -> Any:
        return self._get_store().remember(key, seconds, callback)

    def remember_forever(self, key: str, callback: Callable) -> Any:
        return self._get_store().remember_forever(key, callback)

    def keys(self) -> list[str]:
        """Return the original keys of all live entries in the current store."""
        return list(self._get_store().keys())

    def prune(self) -> int:
        """Delete expired entries (array and file stores). Returns how many were removed."""
        return self._get_store().prune()


Cache = _CacheManager()
