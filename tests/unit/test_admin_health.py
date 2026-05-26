"""Tests for hunt.admin.controllers.health."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from hunt.admin.controllers.health import _storage_size

# ---------------------------------------------------------------------------
# _storage_size
# ---------------------------------------------------------------------------


class TestStorageSize:
    def test_bytes(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"hello")
        result = _storage_size(tmp_path)
        assert result == "5 B"

    def test_kilobytes(self, tmp_path):
        (tmp_path / "large.txt").write_bytes(b"x" * 2048)
        result = _storage_size(tmp_path)
        assert "KB" in result

    def test_megabytes(self, tmp_path):
        (tmp_path / "big.txt").write_bytes(b"x" * (2 * 1024 * 1024))
        result = _storage_size(tmp_path)
        assert "MB" in result

    def test_empty_directory(self, tmp_path):
        result = _storage_size(tmp_path)
        assert result == "0 B"

    def test_nested_files(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "a.txt").write_bytes(b"ab")
        (tmp_path / "b.txt").write_bytes(b"cd")
        result = _storage_size(tmp_path)
        assert result == "4 B"

    def test_missing_path_returns_zero(self, tmp_path):
        # rglob on non-existent path yields nothing — sum is 0, returns "0 B"
        result = _storage_size(tmp_path / "does_not_exist")
        assert result == "0 B"

    def test_gigabytes(self, tmp_path):
        mock_stat = MagicMock()
        mock_stat.st_size = 2 * 1024**3
        mock_file = MagicMock()
        mock_file.is_file.return_value = True
        mock_file.stat.return_value = mock_stat

        with patch.object(Path, "rglob", return_value=[mock_file]):
            result = _storage_size(tmp_path)
        assert "GB" in result

    def test_exception_returns_unknown(self, tmp_path):
        with patch.object(Path, "rglob", side_effect=PermissionError("no access")):
            result = _storage_size(tmp_path)
        assert result == "unknown"


# ---------------------------------------------------------------------------
# _check_database
# ---------------------------------------------------------------------------


class TestCheckDatabase:
    def test_success(self):
        from hunt.admin.controllers.health import _check_database

        mock_row = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_raw = MagicMock(return_value=mock_cursor)

        with patch("hunt.database.connection.raw", mock_raw):
            result = _check_database()

        assert result["ok"] is True
        assert result["label"] == "Connected"
        assert "ms" in result["detail"]

    def test_failure(self):
        from hunt.admin.controllers.health import _check_database

        with patch("hunt.database.connection.raw", side_effect=Exception("connection refused")):
            result = _check_database()

        assert result["ok"] is False
        assert result["label"] == "Error"
        assert "connection refused" in result["detail"]

    def test_detail_truncated_at_120_chars(self):
        from hunt.admin.controllers.health import _check_database

        long_msg = "x" * 200
        with patch("hunt.database.connection.raw", side_effect=Exception(long_msg)):
            result = _check_database()

        assert len(result["detail"]) <= 120


# ---------------------------------------------------------------------------
# _check_cache
# ---------------------------------------------------------------------------


class TestCheckCache:
    def test_success(self):
        from hunt.admin.controllers.health import _check_cache
        from hunt.cache.manager import ArrayStore

        store = ArrayStore()

        with patch("hunt.cache.manager.Cache._get_store", return_value=store):
            result = _check_cache()

        assert result["ok"] is True
        assert result["label"] == "Connected"
        assert result["detail"] == "array"

    def test_readback_mismatch(self):
        from hunt.admin.controllers.health import _check_cache

        store = MagicMock()
        store.put.return_value = None
        store.get.return_value = 999
        store.forget.return_value = None

        with patch("hunt.cache.manager.Cache._get_store", return_value=store):
            result = _check_cache()

        assert result["ok"] is False
        assert "mismatch" in result["detail"].lower()

    def test_exception_returns_error(self):
        from hunt.admin.controllers.health import _check_cache

        with patch("hunt.cache.manager.Cache._get_store", side_effect=Exception("redis unavailable")):
            result = _check_cache()

        assert result["ok"] is False
        assert "redis unavailable" in result["detail"]

    def test_driver_name_from_class(self, tmp_path):
        from hunt.admin.controllers.health import _check_cache
        from hunt.cache.manager import FileStore

        store = FileStore(tmp_path / "cache")

        with patch("hunt.cache.manager.Cache._get_store", return_value=store):
            result = _check_cache()

        assert result["ok"] is True
        assert result["detail"] == "file"


# ---------------------------------------------------------------------------
# _check_queue
# ---------------------------------------------------------------------------


class TestCheckQueue:
    def test_success(self):
        from hunt.admin.controllers.health import _check_queue

        mock_row = MagicMock()
        mock_row.cnt = 5
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_raw = MagicMock(return_value=mock_cursor)

        with patch("hunt.database.connection.raw", mock_raw):
            result = _check_queue()

        assert result["ok"] is True
        assert "5 pending" in result["detail"]

    def test_zero_pending(self):
        from hunt.admin.controllers.health import _check_queue

        mock_row = MagicMock()
        mock_row.cnt = 0
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = mock_row
        mock_raw = MagicMock(return_value=mock_cursor)

        with patch("hunt.database.connection.raw", mock_raw):
            result = _check_queue()

        assert result["ok"] is True
        assert "0 pending" in result["detail"]

    def test_missing_jobs_table_sqlite(self):
        from hunt.admin.controllers.health import _check_queue

        with patch("hunt.database.connection.raw", side_effect=Exception("no such table: jobs")):
            result = _check_queue()

        assert result["ok"] is None
        assert result["label"] == "Not configured"

    def test_missing_jobs_table_mysql(self):
        from hunt.admin.controllers.health import _check_queue

        with patch("hunt.database.connection.raw", side_effect=Exception("Table 'app.jobs' doesn't exist")):
            result = _check_queue()

        assert result["ok"] is None
        assert "migrations" in result["detail"]

    def test_other_db_error(self):
        from hunt.admin.controllers.health import _check_queue

        with patch("hunt.database.connection.raw", side_effect=Exception("connection timed out")):
            result = _check_queue()

        assert result["ok"] is False
        assert "connection timed out" in result["detail"]

    def test_fetchone_returns_none(self):
        from hunt.admin.controllers.health import _check_queue

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_raw = MagicMock(return_value=mock_cursor)

        with patch("hunt.database.connection.raw", mock_raw):
            result = _check_queue()

        assert result["ok"] is True
        assert "0 pending" in result["detail"]
