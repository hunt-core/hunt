"""Regression tests for CACHE-01, CACHE-02 and the cache part of REDIS-L1 / ADM-L12."""

from __future__ import annotations

import datetime
import fnmatch
import json
import math
import threading
import time
from decimal import Decimal

import pytest

from hunt.cache import codec
from hunt.cache.manager import ArrayStore, FileStore, RedisStore, _CacheManager


class FakeRedis:
    """Minimal in-memory stand-in for the redis-py calls RedisStore makes."""

    def __init__(self, decode_responses=False):
        self.data: dict[str, bytes] = {}
        self.expiry: dict[str, int] = {}
        self.decode = decode_responses
        self.lock = threading.Lock()

    def _out(self, value):
        if value is None:
            return None
        return value.decode() if self.decode else value

    @staticmethod
    def _raw(value):
        return value if isinstance(value, bytes) else str(value).encode()

    def get(self, key):
        return self._out(self.data.get(key))

    def set(self, key, value, ex=None, nx=False):
        with self.lock:
            if nx and key in self.data:
                return None
            self.data[key] = self._raw(value)
            if ex is None:
                self.expiry.pop(key, None)
            else:
                assert isinstance(ex, int) and ex > 0
                self.expiry[key] = ex
            return True

    def delete(self, *keys):
        removed = 0
        for key in keys:
            key = key.decode() if isinstance(key, bytes) else key
            removed += self.data.pop(key, None) is not None
            self.expiry.pop(key, None)
        return removed

    def exists(self, key):
        return int(key in self.data)

    def incrby(self, key, amount):
        with self.lock:
            value = int(self.data.get(key, b"0")) + amount
            self.data[key] = str(value).encode()  # TTL untouched, like Redis
            return value

    def ttl(self, key):
        if key not in self.data:
            return -2
        return self.expiry.get(key, -1)

    def mget(self, keys):
        return [self.get(k) for k in keys]

    def scan(self, cursor=0, match="*", count=100):
        keys = [k for k in self.data if fnmatch.fnmatchcase(k, match)]
        return 0, [k if self.decode else k.encode() for k in keys]

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, client):
        self.client = client
        self.ops = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return record

    def execute(self):
        results = [getattr(self.client, name)(*args, **kwargs) for name, args, kwargs in self.ops]
        self.ops = []
        return results


@pytest.fixture(params=["array", "file", "redis", "redis-decoded"])
def store(request, tmp_path):
    if request.param == "array":
        return ArrayStore()
    if request.param == "file":
        return FileStore(tmp_path / "cache")
    return RedisStore(client=FakeRedis(decode_responses=request.param == "redis-decoded"), prefix="t:")


@pytest.fixture(params=["array", "file"])
def local_store(request, tmp_path):
    return ArrayStore() if request.param == "array" else FileStore(tmp_path / "cache")


def _ttl_left(store, key):
    """Remaining TTL in seconds, or None when the key never expires."""
    if isinstance(store, RedisStore):
        ttl = store._redis.ttl(store._key(key))
        return None if ttl == -1 else ttl
    if isinstance(store, FileStore):
        expires_at = json.loads(store._cache_file(key).read_text())["expires_at"]
    else:
        expires_at = store._data[key][1]
    return None if not expires_at else expires_at - time.time()


def _run_threads(count, target):
    errors = []

    def wrapped(i):
        try:
            target(i)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "threads deadlocked"
    assert errors == []


# ---------------------------------------------------------------------------
# CACHE-01: counters, add, locks
# ---------------------------------------------------------------------------


class TestIncrement:
    def test_increment_preserves_ttl(self, store):
        store.put("k", 1, 60)
        assert store.increment("k") == 2
        assert store.decrement("k", 5) == -3
        assert store.get("k") == -3
        left = _ttl_left(store, "k")
        assert left is not None and 0 < left <= 60

    def test_increment_of_forever_key_stays_forever(self, store):
        store.forever("k", 10)
        assert store.increment("k", 5) == 15
        assert _ttl_left(store, "k") is None

    def test_increment_of_missing_key_starts_at_zero(self, store):
        assert store.increment("fresh") == 1
        assert store.get("fresh") == 1

    def test_increment_after_expiry_restarts_without_old_ttl(self, local_store, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        local_store.put("k", 41, 10)
        now[0] += 11
        assert local_store.increment("k") == 1
        assert _ttl_left(local_store, "k") is None

    def test_threaded_increments_are_not_lost(self, local_store):
        _run_threads(4, lambda _i: [local_store.increment("n") for _ in range(300)])
        assert local_store.get("n") == 1200

    def test_two_file_stores_on_one_directory_share_the_lock(self, tmp_path):
        # Separate FileStore objects stand in for separate worker processes.
        stores = [FileStore(tmp_path / "cache") for _ in range(4)]
        _run_threads(4, lambda i: [stores[i].increment("n") for _ in range(200)])
        assert stores[0].get("n") == 800


class TestAdd:
    def test_add_is_atomic_under_threads(self, local_store):
        wins = []
        barrier = threading.Barrier(8)

        def attempt(i):
            barrier.wait(10)
            if local_store.add("lock", i, 30):
                wins.append(i)

        _run_threads(8, attempt)
        assert len(wins) == 1
        assert local_store.get("lock") == wins[0]

    def test_add_across_file_store_instances(self, tmp_path):
        stores = [FileStore(tmp_path / "cache") for _ in range(6)]
        wins = []
        barrier = threading.Barrier(6)

        def attempt(i):
            barrier.wait(10)
            if stores[i].add("lock", i):
                wins.append(i)

        _run_threads(6, attempt)
        assert len(wins) == 1

    def test_add_semantics(self, store):
        assert store.add("k", "first", 60) is True
        assert store.add("k", "second", 60) is False
        assert store.get("k") == "first"
        left = _ttl_left(store, "k")
        assert left is not None and 0 < left <= 60
        assert store.add("forever", 1) is True
        assert _ttl_left(store, "forever") is None

    def test_add_succeeds_once_entry_expired(self, local_store, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        assert local_store.add("k", "a", 10) is True
        now[0] += 11
        assert local_store.add("k", "b", 10) is True
        assert local_store.get("k") == "b"

    def test_add_treats_cached_none_as_present(self, store):
        store.put("k", None)
        assert store.add("k", "x") is False


class TestFileStoreConcurrency:
    def test_concurrent_writers_to_one_key_do_not_crash(self, tmp_path):
        store = FileStore(tmp_path / "cache")
        _run_threads(8, lambda i: [store.put("same", {"writer": i, "n": n}) for n in range(100)])
        assert set(store.get("same")) == {"writer", "n"}
        leftovers = [p for p in (tmp_path / "cache").rglob("*.tmp")]
        assert leftovers == []

    def test_readers_never_see_partial_files(self, tmp_path):
        store = FileStore(tmp_path / "cache")
        store.put("k", "x" * 2000)
        seen = []
        stop = threading.Event()

        def read(_i):
            while not stop.is_set():
                seen.append(store.get("k", "MISSING"))

        def write(_i):
            for n in range(200):
                store.put("k", str(n) * 2000)
            stop.set()

        _run_threads(3, lambda i: write(i) if i == 0 else read(i))
        assert "MISSING" not in seen

    def test_temp_names_are_unique_per_write(self, tmp_path):
        store = FileStore(tmp_path / "cache")
        f = store._cache_file("k")
        first = store._write_temp(f, "{}")
        second = store._write_temp(f, "{}")
        assert first != second
        for tmp in (first, second):
            (tmp_path / tmp).unlink()


# ---------------------------------------------------------------------------
# CACHE-02: one codec, consistent semantics
# ---------------------------------------------------------------------------

AWARE = datetime.datetime(2026, 3, 4, 5, 6, 7, 890, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
VALUES = [
    datetime.datetime(2026, 1, 2, 3, 4, 5, 678901),
    AWARE,
    datetime.date(2026, 2, 28),
    datetime.time(23, 59, 1),
    Decimal("12345678901234567890.123456789"),
    b"\x00\xff binary \n",
    {"when": AWARE, "rows": [{"id": 1, "price": Decimal("9.99"), "blob": b"ab"}], "none": None},
    {"__hunt__": "datetime", "v": "not really"},
    {"__hunt__": "x"},
    [1, 2.5, "three", True, None],
    "plain",
    0,
    "",
    False,
]


class TestCodec:
    @pytest.mark.parametrize("value", VALUES, ids=lambda v: type(v).__name__)
    def test_values_round_trip_on_every_store(self, store, value):
        store.put("k", value, 60)
        result = store.get("k")
        assert result == value
        assert type(result) is type(value)

    def test_datetime_keeps_its_offset(self, store):
        store.put("k", AWARE)
        assert store.get("k").utcoffset() == datetime.timedelta(hours=2)

    def test_query_rows_with_timestamps_work_on_file_store(self, tmp_path):
        # The shape QueryBuilder.remember() caches; this raised on the file driver.
        rows = [{"id": 1, "created_at": datetime.datetime(2026, 1, 1, 12), "total": Decimal("10.50")}]
        s = FileStore(tmp_path / "cache")
        s.put("q", rows, 60)
        assert s.get("q") == rows

    def test_unsupported_type_raises_on_every_store(self, store):
        with pytest.raises(TypeError, match="not cacheable"):
            store.put("k", object())
        with pytest.raises(TypeError):
            store.add("k2", {"nested": [object()]})
        assert store.has("k") is False
        assert store.has("k2") is False

    def test_tuples_come_back_as_lists_everywhere(self, store):
        store.put("k", (1, 2))
        assert store.get("k") == [1, 2]

    def test_sets_round_trip(self, store):
        store.put("k", {"a", "b"})
        assert store.get("k") == {"a", "b"}

    def test_stored_value_is_isolated_from_later_mutation(self, store):
        value = {"items": [1]}
        store.put("k", value)
        value["items"].append(2)
        got = store.get("k")
        got["items"].append(3)
        assert store.get("k") == {"items": [1]}

    def test_get_many_and_put_many_use_the_codec(self, store):
        store.put_many({"a": Decimal("1.5"), "b": AWARE}, 60)
        assert store.get_many(["a", "b", "c"]) == {"a": Decimal("1.5"), "b": AWARE, "c": None}

    def test_pull_decodes(self, store):
        store.put("k", Decimal("2.5"))
        assert store.pull("k") == Decimal("2.5")
        assert store.pull("k", "gone") == "gone"

    def test_legacy_untagged_json_still_decodes(self):
        assert codec.loads('{"a": [1, {"b": null}]}') == {"a": [1, {"b": None}]}

    def test_nan_and_infinity_round_trip(self):
        assert codec.loads(codec.dumps(float("inf"))) == float("inf")
        assert math.isnan(codec.loads(codec.dumps(float("nan"))))

    def test_legacy_file_payload_without_key_is_readable(self, tmp_path):
        s = FileStore(tmp_path / "cache")
        f = s._cache_file("old")
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"value": {"a": 1}, "expires_at": 0}))
        assert s.get("old") == {"a": 1}
        assert s.increment("n") == 1


class TestNoneAndTtlSemantics:
    def test_remember_caches_none(self, store):
        calls = []

        def compute():
            calls.append(1)
            return None

        assert store.remember("k", 60, compute) is None
        assert store.remember("k", 60, compute) is None
        assert store.remember_forever("k2", compute) is None
        assert store.remember_forever("k2", compute) is None
        assert len(calls) == 2

    def test_remember_caches_falsy_values(self, store):
        calls = []
        for expected in (0, "", [], False):
            key = f"k-{expected!r}"
            for _ in range(2):
                assert store.remember(key, 60, lambda e=expected: calls.append(1) or e) == expected
        assert len(calls) == 4

    def test_has_is_true_for_cached_none(self, store):
        assert store.has("k") is False
        store.put("k", None, 60)
        assert store.has("k") is True
        assert store.get("k", "default") is None

    def test_default_ttl_is_forever(self, store):
        store.put("k", 1)
        assert store.get("k") == 1
        assert _ttl_left(store, "k") is None

    @pytest.mark.parametrize("ttl", [0, -1, -3600, 0.0])
    def test_non_positive_ttl_forgets_instead_of_persisting(self, store, ttl):
        store.put("k", "old", 60)
        store.put("k", "new", ttl)
        assert store.has("k") is False
        store.put_many({"a": 1}, ttl)
        assert store.has("a") is False
        assert store.add("b", 1, ttl) is False
        assert store.has("b") is False

    def test_remember_with_non_positive_ttl_does_not_persist(self, store):
        calls = []
        assert store.remember("k", 0, lambda: calls.append(1) or "v") == "v"
        assert store.has("k") is False
        assert store.remember("k", 0, lambda: calls.append(1) or "v") == "v"
        assert len(calls) == 2

    def test_fractional_ttl_is_supported(self, store):
        store.put("k", 1, 0.5)
        assert store.get("k") == 1

    def test_expiry(self, local_store, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        local_store.put("k", "v", 10)
        now[0] += 9
        assert local_store.get("k") == "v"
        now[0] += 2
        assert local_store.get("k") is None
        assert local_store.has("k") is False


class TestRememberLock:
    def test_concurrent_remember_computes_once(self, local_store):
        calls = []
        barrier = threading.Barrier(8)
        results = []

        def compute():
            calls.append(1)
            time.sleep(0.05)
            return {"expensive": True}

        def work(_i):
            barrier.wait(10)
            results.append(local_store.remember("k", 60, compute))

        _run_threads(8, work)
        assert len(calls) == 1
        assert results == [{"expensive": True}] * 8

    def test_remember_across_file_store_instances_computes_once(self, tmp_path):
        stores = [FileStore(tmp_path / "cache") for _ in range(4)]
        calls = []
        barrier = threading.Barrier(4)

        def compute():
            calls.append(1)
            time.sleep(0.05)
            return 7

        def work(i):
            barrier.wait(10)
            assert stores[i].remember("k", 60, compute) == 7

        _run_threads(4, work)
        assert len(calls) == 1

    def test_nested_remember_does_not_deadlock(self, local_store):
        def outer():
            return [local_store.remember(f"inner-{i}", 60, lambda i=i: i) for i in range(200)]

        done = []
        t = threading.Thread(target=lambda: done.append(local_store.remember("outer", 60, outer)))
        t.start()
        t.join(20)
        assert done == [list(range(200))]

    def test_callback_error_releases_the_lock(self, local_store):
        def boom():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            local_store.remember("k", 60, boom)
        assert local_store.remember("k", 60, lambda: "ok") == "ok"


class TestPrune:
    def test_prune_removes_only_expired(self, local_store, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        local_store.put("short", 1, 10)
        local_store.put("long", 2, 1000)
        local_store.forever("forever", 3)
        now[0] += 11
        assert local_store.prune() == 1
        assert local_store.prune() == 0
        assert sorted(local_store.keys()) == ["forever", "long"]
        if isinstance(local_store, FileStore):
            assert not local_store._cache_file("short").exists()
        else:
            assert "short" not in local_store._data

    def test_expired_entries_are_pruned_periodically_on_write(self, tmp_path, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        for s in (ArrayStore(prune_every=5), FileStore(tmp_path / "cache", prune_every=5)):
            for i in range(3):
                s.put(f"stale-{i}", i, 10)
            now[0] += 11
            for i in range(5):
                s.put(f"fresh-{i}", i, 1000)
            # Never read, yet gone: purged by the periodic prune, not by a read.
            if isinstance(s, FileStore):
                assert not any(s._cache_file(f"stale-{i}").exists() for i in range(3))
            else:
                assert not any(k.startswith("stale-") for k in s._data)
            assert len(list(s.keys())) == 5

    def test_manager_exposes_prune_and_keys(self):
        mgr = _CacheManager()
        mgr.configure("array")
        mgr.put("a", 1)
        assert mgr.keys() == ["a"]
        assert mgr.prune() == 0

    def test_redis_prune_is_a_noop(self):
        assert RedisStore(client=FakeRedis()).prune() == 0


# ---------------------------------------------------------------------------
# Original keys (ADM-L12 support)
# ---------------------------------------------------------------------------


class TestKeys:
    def test_keys_yields_original_keys(self, store):
        store.put("users:1", 1, 60)
        store.forever("settings/theme", "dark")
        store.increment("hits")
        store.add("lock:job", 1)
        assert sorted(store.keys()) == ["hits", "lock:job", "settings/theme", "users:1"]
        store.forget("hits")
        assert "hits" not in list(store.keys())

    def test_file_payload_records_the_original_key(self, tmp_path):
        s = FileStore(tmp_path / "cache")
        s.put("my key", {"a": 1}, 60)
        payload = json.loads(s._cache_file("my key").read_text())
        assert payload["key"] == "my key"
        assert payload["value"] == {"a": 1}
        # A key returned by keys() can be passed straight back to forget().
        (key,) = s.keys()
        s.forget(key)
        assert list(s.keys()) == []

    def test_file_keys_skip_expired_legacy_and_lock_files(self, tmp_path, monkeypatch):
        now = [1_000_000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        s = FileStore(tmp_path / "cache")
        s.put("gone", 1, 10)
        s.put("live", 1, 1000)
        s.increment("counter")  # creates a lock file
        legacy = s._cache_file("legacy")
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"value": 1, "expires_at": 0}))
        now[0] += 11
        assert sorted(s.keys()) == ["counter", "live"]

    def test_flush_empties_the_store_and_it_keeps_working(self, store):
        store.put("a", 1)
        store.increment("n")
        store.flush()
        assert list(store.keys()) == []
        assert store.increment("n") == 1
        assert store.add("a", 2) is True


# ---------------------------------------------------------------------------
# Redis store
# ---------------------------------------------------------------------------


class TestRedisStore:
    def test_importable_and_usable_without_redis_py(self):
        import sys

        assert "redis" not in sys.modules or sys.modules["redis"] is not None
        s = RedisStore(client=FakeRedis(), prefix="p:")
        s.put("k", {"a": 1}, 30)
        assert s.get("k") == {"a": 1}

    def test_missing_redis_py_raises_import_error(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "redis", None)
        with pytest.raises(ImportError, match="redis"):
            RedisStore()

    def test_values_are_stored_as_tagged_json_not_str(self):
        client = FakeRedis()
        s = RedisStore(client=client, prefix="p:")
        s.put("d", Decimal("1.10"))
        assert json.loads(client.data["p:d"]) == {"__hunt__": "decimal", "v": "1.10"}

    def test_ttl_is_a_positive_integer(self):
        client = FakeRedis()
        s = RedisStore(client=client, prefix="p:")
        s.put("k", 1, 0.2)
        assert client.expiry["p:k"] == 1
        s.put("k", 1, 90)
        assert client.expiry["p:k"] == 90

    def test_flush_and_keys_are_limited_to_the_prefix(self):
        client = FakeRedis()
        client.set("session:abc", "keep")
        s = RedisStore(client=client, prefix="p:")
        s.put("a", 1)
        assert list(s.keys()) == ["a"]
        s.flush()
        assert client.data == {"session:abc": b"keep"}

    def test_corrupt_value_reads_as_missing(self):
        client = FakeRedis()
        client.set("p:bad", "{not json")
        s = RedisStore(client=client, prefix="p:")
        assert s.get("bad", "default") == "default"
        assert s.get_many(["bad"]) == {"bad": None}
        assert s.pull("bad", "default") == "default"

    def test_manager_uses_shared_factory_when_only_env_is_given(self, monkeypatch):
        shared = FakeRedis(decode_responses=True)
        monkeypatch.setattr("hunt.redis_connection.get_redis", lambda: shared)
        mgr = _CacheManager()
        mgr.configure("redis", prefix="app:")
        assert mgr._get_store()._redis is shared
        mgr.put("k", AWARE, 60)
        assert mgr.get("k") == AWARE
        assert mgr.increment("n") == 1
        assert sorted(mgr.keys()) == ["k", "n"]
