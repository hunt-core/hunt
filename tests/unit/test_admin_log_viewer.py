"""Tests for hunt.admin.controllers.logs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from hunt.admin.application import Admin
from hunt.admin.controllers.logs import _log_path, _parse_line, _tail

# ---------------------------------------------------------------------------
# _log_path
# ---------------------------------------------------------------------------


class TestLogPath:
    def test_default_is_storage_logs(self, monkeypatch):
        monkeypatch.delenv("LOG_PATH", raising=False)
        path = _log_path()
        assert path == Path.cwd() / "storage" / "logs" / "hunt.log"

    def test_env_override(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "custom.log")
        monkeypatch.setenv("LOG_PATH", custom)
        assert _log_path() == Path(custom)

    def test_empty_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("LOG_PATH", "")
        path = _log_path()
        assert path == Path.cwd() / "storage" / "logs" / "hunt.log"


# ---------------------------------------------------------------------------
# _parse_line
# ---------------------------------------------------------------------------


class TestParseLine:
    def test_json_format(self):
        line = json.dumps(
            {
                "ts": "2024-01-15T12:00:00.000Z",
                "level": "INFO",
                "message": "Hello world",
                "request_id": "abc-123",
            }
        )
        result = _parse_line(line)
        assert result is not None
        assert result["ts"] == "2024-01-15T12:00:00.000Z"
        assert result["level"] == "info"
        assert result["message"] == "Hello world"
        assert result["request_id"] == "abc-123"
        assert result["exception"] == ""

    def test_json_with_exception(self):
        line = json.dumps(
            {
                "ts": "2024-01-15T12:00:00.000Z",
                "level": "error",
                "message": "Crash",
                "exception": "Traceback...",
            }
        )
        result = _parse_line(line)
        assert result is not None
        assert result["exception"] == "Traceback..."

    def test_json_missing_fields_use_defaults(self):
        result = _parse_line("{}")
        assert result is not None
        assert result["ts"] == ""
        assert result["level"] == "info"
        assert result["message"] == ""
        assert result["request_id"] == ""
        assert result["exception"] == ""

    def test_text_format(self):
        line = "[2024-01-15 12:00:00] WARNING  Something went wrong"
        result = _parse_line(line)
        assert result is not None
        assert result["ts"] == "2024-01-15 12:00:00"
        assert result["level"] == "warning"
        assert result["message"] == "Something went wrong"
        assert result["request_id"] == ""
        assert result["exception"] == ""

    def test_text_format_info(self):
        line = "[2024-06-01 09:30:00] INFO     Server started"
        result = _parse_line(line)
        assert result is not None
        assert result["level"] == "info"
        assert result["message"] == "Server started"

    def test_empty_line_returns_none(self):
        assert _parse_line("") is None
        assert _parse_line("   ") is None
        assert _parse_line("\n") is None

    def test_malformed_json_returns_none(self):
        assert _parse_line("{invalid json") is None

    def test_unrecognised_format_returns_none(self):
        assert _parse_line("just some random text") is None
        assert _parse_line("no timestamp here") is None

    def test_json_null_request_id_becomes_empty_string(self):
        line = json.dumps({"level": "debug", "message": "x", "request_id": None})
        result = _parse_line(line)
        assert result is not None
        assert result["request_id"] == ""

    def test_strips_whitespace(self):
        line = "   [2024-01-15 12:00:00] INFO     padded   "
        result = _parse_line(line)
        assert result is not None
        assert result["level"] == "info"


# ---------------------------------------------------------------------------
# _tail
# ---------------------------------------------------------------------------


class TestTail:
    def test_empty_file_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.log"
        f.write_bytes(b"")
        assert _tail(f, 10) == []

    def test_returns_last_n_lines(self, tmp_path):
        f = tmp_path / "app.log"
        lines = [f"line {i}" for i in range(20)]
        f.write_text("\n".join(lines))
        result = _tail(f, 5)
        assert result == lines[-5:]

    def test_returns_all_when_fewer_than_n(self, tmp_path):
        f = tmp_path / "app.log"
        f.write_text("line 1\nline 2\nline 3")
        result = _tail(f, 100)
        assert "line 1" in result
        assert "line 3" in result

    def test_single_line_file(self, tmp_path):
        f = tmp_path / "app.log"
        f.write_text("only line")
        result = _tail(f, 10)
        assert result == ["only line"]

    def test_unicode_content(self, tmp_path):
        f = tmp_path / "app.log"
        f.write_text("line with emoji 🚀\nnormal line")
        result = _tail(f, 5)
        assert any("emoji" in r for r in result)


# ---------------------------------------------------------------------------
# index controller
# ---------------------------------------------------------------------------


class TestLogsIndexController:
    def _make_request(self, query_params: dict | None = None):
        request = MagicMock()
        params = query_params or {}
        request.query.side_effect = lambda key: params.get(key)
        return request

    def test_missing_log_file_sets_missing_flag(self, tmp_path, monkeypatch):
        from hunt.admin.controllers.logs import index

        monkeypatch.setenv("LOG_PATH", str(tmp_path / "nonexistent.log"))
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        with (
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(self._make_request())

        call_kwargs = render.call_args[0][1]
        assert call_kwargs["missing"] is True
        assert call_kwargs["entries"] == []

    def test_level_filter_applied(self, tmp_path, monkeypatch):
        from hunt.admin.controllers.logs import index

        log_file = tmp_path / "hunt.log"
        log_file.write_text(
            json.dumps({"level": "error", "message": "an error", "ts": "t"}) + "\n"
            + json.dumps({"level": "info", "message": "an info", "ts": "t"}) + "\n"
        )
        monkeypatch.setenv("LOG_PATH", str(log_file))
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        with (
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(self._make_request({"level": "error"}))

        ctx = render.call_args[0][1]
        assert ctx["level_filter"] == "error"
        assert all(e["level"] == "error" for e in ctx["entries"])

    def test_invalid_level_filter_ignored(self, tmp_path, monkeypatch):
        from hunt.admin.controllers.logs import index

        log_file = tmp_path / "hunt.log"
        log_file.write_text(json.dumps({"level": "info", "message": "msg", "ts": "t"}) + "\n")
        monkeypatch.setenv("LOG_PATH", str(log_file))
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        with (
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(self._make_request({"level": "invalid"}))

        ctx = render.call_args[0][1]
        assert ctx["level_filter"] == ""

    def test_search_filter_applied(self, tmp_path, monkeypatch):
        from hunt.admin.controllers.logs import index

        log_file = tmp_path / "hunt.log"
        log_file.write_text(
            json.dumps({"level": "info", "message": "database connection", "ts": "t"}) + "\n"
            + json.dumps({"level": "info", "message": "user logged in", "ts": "t"}) + "\n"
        )
        monkeypatch.setenv("LOG_PATH", str(log_file))
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        with (
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(self._make_request({"search": "database"}))

        ctx = render.call_args[0][1]
        assert len(ctx["entries"]) == 1
        assert "database" in ctx["entries"][0]["message"]

    def test_entries_returned_newest_first(self, tmp_path, monkeypatch):
        from hunt.admin.controllers.logs import index

        log_file = tmp_path / "hunt.log"
        log_file.write_text(
            json.dumps({"level": "info", "message": "first", "ts": "2024-01-01T00:00:00Z"}) + "\n"
            + json.dumps({"level": "info", "message": "second", "ts": "2024-01-02T00:00:00Z"}) + "\n"
        )
        monkeypatch.setenv("LOG_PATH", str(log_file))
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        with (
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(self._make_request())

        ctx = render.call_args[0][1]
        assert ctx["entries"][0]["message"] == "second"
        assert ctx["entries"][1]["message"] == "first"
