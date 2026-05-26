"""Tests for hunt.admin.controllers.cache_inspector."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

from hunt.admin.application import Admin
from hunt.admin.controllers.cache_inspector import (
    _list_array,
    _list_file,
    _truncate,
)
from hunt.cache.manager import ArrayStore, FileStore

# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_string_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_string_truncated(self):
        long = "x" * 200
        result = _truncate(long)
        assert result.endswith("…")
        assert len(result) == 121  # 120 + ellipsis

    def test_non_string_serialised_as_json(self):
        result = _truncate(42)
        assert result == "42"

    def test_dict_serialised(self):
        result = _truncate({"a": 1})
        assert '"a"' in result

    def test_none_serialised(self):
        result = _truncate(None)
        assert result == "null"

    def test_list_serialised(self):
        result = _truncate([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_custom_max_len(self):
        result = _truncate("abcdefghij", max_len=5)
        assert result == "abcde…"

    def test_exactly_max_len_not_truncated(self):
        s = "a" * 120
        result = _truncate(s)
        assert result == s
        assert not result.endswith("…")


# ---------------------------------------------------------------------------
# _list_array
# ---------------------------------------------------------------------------


class TestListArray:
    def test_empty_store_returns_empty(self):
        store = ArrayStore()
        result = _list_array(store)
        assert result == []

    def test_live_entry_included(self):
        store = ArrayStore()
        store.put("key1", "value1", 3600)
        result = _list_array(store)
        assert len(result) == 1
        assert result[0]["key"] == "key1"
        assert "value1" in result[0]["value"]

    def test_expired_entry_excluded(self):
        store = ArrayStore()
        store._data["expired_key"] = ("stale_value", time.time() - 1)
        result = _list_array(store)
        assert all(e["key"] != "expired_key" for e in result)

    def test_forever_entry(self):
        store = ArrayStore()
        store.forever("perm_key", "perm_value")
        result = _list_array(store)
        assert len(result) == 1
        entry = result[0]
        assert entry["key"] == "perm_key"
        assert entry["forever"] is True
        assert entry["ttl"] is None

    def test_results_sorted_by_key(self):
        store = ArrayStore()
        store.put("banana", 1, 3600)
        store.put("apple", 2, 3600)
        store.put("cherry", 3, 3600)
        result = _list_array(store)
        keys = [e["key"] for e in result]
        assert keys == sorted(keys)

    def test_ttl_approximate(self):
        store = ArrayStore()
        store.put("key1", "v", 100)
        result = _list_array(store)
        assert result[0]["ttl"] is not None
        assert 95 <= result[0]["ttl"] <= 100

    def test_multiple_mixed_entries(self):
        store = ArrayStore()
        store.put("live_key", "live", 3600)
        store._data["dead_key"] = ("dead", time.time() - 10)
        store.forever("perm_key", "perm")
        result = _list_array(store)
        keys = [e["key"] for e in result]
        assert "live_key" in keys
        assert "perm_key" in keys
        assert "dead_key" not in keys


# ---------------------------------------------------------------------------
# _list_file
# ---------------------------------------------------------------------------


class TestListFile:
    def test_empty_store_returns_empty(self, tmp_path):
        store = FileStore(tmp_path)
        result = _list_file(store)
        assert result == []

    def test_live_file_entry_included(self, tmp_path):
        store = FileStore(tmp_path)
        store.put("mykey", "myvalue", 3600)
        result = _list_file(store)
        assert len(result) == 1
        assert "myvalue" in result[0]["value"]

    def test_expired_file_excluded(self, tmp_path):
        store = FileStore(tmp_path)
        cache_file = store._cache_file("expired")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"value": "old", "expires_at": time.time() - 1}))
        result = _list_file(store)
        assert result == []

    def test_invalid_json_file_skipped(self, tmp_path):
        store = FileStore(tmp_path)
        bad_file = tmp_path / "ab" / "abcdef"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("not json {{{")
        result = _list_file(store)
        assert result == []

    def test_forever_entry_included(self, tmp_path):
        store = FileStore(tmp_path)
        store.forever("perm", "permanent_value")
        result = _list_file(store)
        assert len(result) == 1
        entry = result[0]
        assert entry["forever"] is True
        assert entry["ttl"] is None

    def test_results_sorted(self, tmp_path):
        store = FileStore(tmp_path)
        for key in ["zzz", "aaa", "mmm"]:
            store.put(key, key, 3600)
        result = _list_file(store)
        names = [e["key"] for e in result]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# delete and flush via mocked _store
# ---------------------------------------------------------------------------


class TestDeleteAndFlushActions:
    def _make_request(self, input_data: dict | None = None):
        request = MagicMock()
        data = input_data or {}
        request.input.side_effect = lambda key: data.get(key)
        request._session = None
        return request

    def test_delete_calls_store_forget(self):
        from hunt.admin.controllers.cache_inspector import delete

        store = MagicMock()
        request = self._make_request({"key": "mykey"})

        with (
            patch("hunt.admin.controllers.cache_inspector._store", return_value=store),
            patch.object(Admin, "prefix", "/admin"),
        ):
            delete(request)

        store.forget.assert_called_once_with("mykey")

    def test_delete_empty_key_skips_forget(self):
        from hunt.admin.controllers.cache_inspector import delete

        store = MagicMock()
        request = self._make_request({"key": ""})

        with (
            patch("hunt.admin.controllers.cache_inspector._store", return_value=store),
            patch.object(Admin, "prefix", "/admin"),
        ):
            delete(request)

        store.forget.assert_not_called()

    def test_flush_calls_store_flush(self):
        from hunt.admin.controllers.cache_inspector import flush

        store = MagicMock()
        request = self._make_request()

        with (
            patch("hunt.admin.controllers.cache_inspector._store", return_value=store),
            patch.object(Admin, "prefix", "/admin"),
        ):
            flush(request)

        store.flush.assert_called_once()

    def test_delete_exception_does_not_raise(self):
        from hunt.admin.controllers.cache_inspector import delete

        store = MagicMock()
        store.forget.side_effect = Exception("oops")
        request = self._make_request({"key": "k"})

        with (
            patch("hunt.admin.controllers.cache_inspector._store", return_value=store),
            patch.object(Admin, "prefix", "/admin"),
        ):
            delete(request)  # should not raise
