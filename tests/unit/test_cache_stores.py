"""Tests for ArrayStore and FileStore cache backends."""

from __future__ import annotations

import time

from hunt.cache.manager import ArrayStore, FileStore

# ---------------------------------------------------------------------------
# ArrayStore
# ---------------------------------------------------------------------------


class TestArrayStore:
    def setup_method(self):
        self.store = ArrayStore()

    def test_put_and_get(self):
        self.store.put("key", "value")
        assert self.store.get("key") == "value"

    def test_get_missing_returns_default(self):
        assert self.store.get("missing") is None
        assert self.store.get("missing", "fallback") == "fallback"

    def test_put_with_ttl_expires(self):
        self.store._data["expiring"] = ("v", time.time() - 1)
        assert self.store.get("expiring") is None

    def test_put_with_future_ttl_accessible(self):
        self.store.put("fresh", "v", 3600)
        assert self.store.get("fresh") == "v"

    def test_forever_never_expires(self):
        self.store.forever("perm", "permanent")
        entry = self.store._data["perm"]
        assert entry[1] == 0  # expires_at = 0 means forever
        assert self.store.get("perm") == "permanent"

    def test_forget_removes_key(self):
        self.store.put("k", "v")
        self.store.forget("k")
        assert self.store.get("k") is None

    def test_forget_missing_key_no_error(self):
        self.store.forget("nonexistent")

    def test_flush_clears_all(self):
        self.store.put("a", 1)
        self.store.put("b", 2)
        self.store.flush()
        assert self.store.get("a") is None
        assert self.store.get("b") is None

    def test_has_returns_true_for_existing(self):
        self.store.put("k", "v")
        assert self.store.has("k") is True

    def test_has_returns_false_for_missing(self):
        assert self.store.has("missing") is False

    def test_has_returns_false_for_expired(self):
        self.store._data["dead"] = ("v", time.time() - 1)
        assert self.store.has("dead") is False

    def test_add_stores_when_missing(self):
        result = self.store.add("new_key", "new_value", 60)
        assert result is True
        assert self.store.get("new_key") == "new_value"

    def test_add_does_not_overwrite_existing(self):
        self.store.put("existing", "original")
        result = self.store.add("existing", "overwrite")
        assert result is False
        assert self.store.get("existing") == "original"

    def test_pull_returns_and_removes(self):
        self.store.put("k", "v")
        value = self.store.pull("k")
        assert value == "v"
        assert self.store.get("k") is None

    def test_pull_missing_returns_default(self):
        assert self.store.pull("missing", "default") == "default"

    def test_increment(self):
        self.store.put("counter", 10)
        result = self.store.increment("counter", 5)
        assert result == 15
        assert self.store.get("counter") == 15

    def test_increment_from_zero(self):
        result = self.store.increment("new_counter")
        assert result == 1

    def test_decrement(self):
        self.store.put("counter", 10)
        result = self.store.decrement("counter", 3)
        assert result == 7

    def test_get_many(self):
        self.store.put("a", 1)
        self.store.put("b", 2)
        result = self.store.get_many(["a", "b", "c"])
        assert result["a"] == 1
        assert result["b"] == 2
        assert result["c"] is None

    def test_put_many(self):
        self.store.put_many({"x": 10, "y": 20}, seconds=60)
        assert self.store.get("x") == 10
        assert self.store.get("y") == 20

    def test_remember_caches_callback_result(self):
        calls = []

        def compute():
            calls.append(1)
            return 42

        result = self.store.remember("memo", 60, compute)
        assert result == 42
        assert len(calls) == 1

        result2 = self.store.remember("memo", 60, compute)
        assert result2 == 42
        assert len(calls) == 1  # not called again

    def test_remember_forever(self):
        calls = []
        result = self.store.remember_forever("perm_memo", lambda: calls.append(1) or 99)
        assert result == 99
        result2 = self.store.remember_forever("perm_memo", lambda: 0)
        assert result2 == 99

    def test_stores_complex_types(self):
        data = {"key": [1, 2, 3], "nested": {"a": True}}
        self.store.put("complex", data)
        assert self.store.get("complex") == data

    def test_stores_none_value(self):
        self.store.put("null_key", None)
        # None value is indistinguishable from missing via get()
        # but it's in _data
        assert "null_key" in self.store._data


# ---------------------------------------------------------------------------
# FileStore
# ---------------------------------------------------------------------------


class TestFileStore:
    def setup_method(self, tmp_path_factory):
        pass

    def _store(self, tmp_path):
        return FileStore(tmp_path / "cache")

    def test_put_and_get(self, tmp_path):
        store = self._store(tmp_path)
        store.put("hello", "world")
        assert store.get("hello") == "world"

    def test_get_missing_returns_default(self, tmp_path):
        store = self._store(tmp_path)
        assert store.get("missing") is None
        assert store.get("missing", 99) == 99

    def test_expired_file_not_returned(self, tmp_path):
        import json

        store = self._store(tmp_path)
        store.put("expires", "old", 1)
        # Force expiry by writing a stale file directly
        f = store._cache_file("expires")
        f.write_text(json.dumps({"value": "old", "expires_at": time.time() - 5}))
        assert store.get("expires") is None

    def test_forever(self, tmp_path):
        store = self._store(tmp_path)
        store.forever("perm", "value")
        assert store.get("perm") == "value"
        import json

        payload = json.loads(store._cache_file("perm").read_text())
        assert payload["expires_at"] == 0

    def test_forget(self, tmp_path):
        store = self._store(tmp_path)
        store.put("k", "v")
        assert store._cache_file("k").exists()
        store.forget("k")
        assert store.get("k") is None

    def test_forget_missing_no_error(self, tmp_path):
        store = self._store(tmp_path)
        store.forget("nonexistent")

    def test_flush(self, tmp_path):
        store = self._store(tmp_path)
        store.put("a", 1)
        store.put("b", 2)
        store.flush()
        assert store.get("a") is None
        assert store.get("b") is None

    def test_has(self, tmp_path):
        store = self._store(tmp_path)
        assert store.has("k") is False
        store.put("k", "v")
        assert store.has("k") is True

    def test_add(self, tmp_path):
        store = self._store(tmp_path)
        assert store.add("k", "v") is True
        assert store.add("k", "other") is False
        assert store.get("k") == "v"

    def test_pull(self, tmp_path):
        store = self._store(tmp_path)
        store.put("k", "v")
        assert store.pull("k") == "v"
        assert store.get("k") is None

    def test_increment_and_decrement(self, tmp_path):
        store = self._store(tmp_path)
        assert store.increment("n") == 1
        assert store.increment("n", 4) == 5
        assert store.decrement("n", 2) == 3

    def test_put_many_and_get_many(self, tmp_path):
        store = self._store(tmp_path)
        store.put_many({"x": 10, "y": 20})
        result = store.get_many(["x", "y", "z"])
        assert result["x"] == 10
        assert result["y"] == 20
        assert result["z"] is None

    def test_complex_value(self, tmp_path):
        store = self._store(tmp_path)
        data = {"nested": [1, 2, {"key": True}]}
        store.put("complex", data)
        assert store.get("complex") == data

    def test_different_keys_use_different_files(self, tmp_path):
        store = self._store(tmp_path)
        store.put("key_a", "value_a")
        store.put("key_b", "value_b")
        f_a = store._cache_file("key_a")
        f_b = store._cache_file("key_b")
        assert f_a != f_b
        assert store.get("key_a") == "value_a"
        assert store.get("key_b") == "value_b"

    def test_creates_directory_on_init(self, tmp_path):
        cache_dir = tmp_path / "deeply" / "nested" / "cache"
        FileStore(cache_dir)
        assert cache_dir.exists()
