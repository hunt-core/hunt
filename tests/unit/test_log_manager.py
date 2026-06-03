"""Tests for hunt.log.manager."""

from __future__ import annotations

import json
import logging
import logging.handlers

import pytest

from hunt.log.manager import _build_channel, _JsonFormatter, _LogManager, _make_formatter

# ---------------------------------------------------------------------------
# _make_formatter
# ---------------------------------------------------------------------------


class TestMakeFormatter:
    def test_returns_formatter(self):
        fmt = _make_formatter()
        assert isinstance(fmt, logging.Formatter)

    def test_custom_format(self):
        fmt = _make_formatter("%(levelname)s %(message)s")
        assert fmt._fmt == "%(levelname)s %(message)s"

    def test_default_datefmt(self):
        fmt = _make_formatter()
        assert fmt.datefmt == "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# _JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    def _make_record(self, message="test message", level=logging.INFO, exc_info=None):
        record = logging.LogRecord(
            name="hunt.test",
            level=level,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=exc_info,
        )
        return record

    def test_basic_output_is_valid_json(self):
        formatter = _JsonFormatter()
        record = self._make_record("Hello")
        output = formatter.format(record)
        payload = json.loads(output)
        assert payload["message"] == "Hello"
        assert "ts" in payload
        assert "level" in payload

    def test_level_lowercased(self):
        formatter = _JsonFormatter()
        record = self._make_record("msg", level=logging.ERROR)
        payload = json.loads(formatter.format(record))
        assert payload["level"] == "error"

    def test_ts_format(self):
        formatter = _JsonFormatter()
        record = self._make_record("msg")
        payload = json.loads(formatter.format(record))
        assert payload["ts"].endswith("Z")
        assert "T" in payload["ts"]

    def test_request_id_included(self):
        import hunt.ctx as ctx_module

        formatter = _JsonFormatter()
        record = self._make_record("msg")
        token = ctx_module.request_id.set("req-abc-123")
        try:
            payload = json.loads(formatter.format(record))
        finally:
            ctx_module.request_id.reset(token)
        assert payload["request_id"] == "req-abc-123"

    def test_request_id_none_when_not_set(self):
        import hunt.ctx as ctx_module

        formatter = _JsonFormatter()
        record = self._make_record("msg")
        # Reset to empty string (default) and check it becomes None
        token = ctx_module.request_id.set("")
        try:
            payload = json.loads(formatter.format(record))
        finally:
            ctx_module.request_id.reset(token)
        assert payload["request_id"] is None

    def test_exception_included_when_present(self):
        formatter = _JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = self._make_record("with exception", exc_info=exc_info)
        payload = json.loads(formatter.format(record))
        assert "exception" in payload
        assert "ValueError" in payload["exception"]

    def test_no_exception_key_when_no_exception(self):
        formatter = _JsonFormatter()
        record = self._make_record("clean message")
        payload = json.loads(formatter.format(record))
        assert "exception" not in payload


# ---------------------------------------------------------------------------
# _build_channel
# ---------------------------------------------------------------------------


class TestBuildChannel:
    def test_file_driver_creates_rotating_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_NON_BLOCKING", "false")  # inspect the underlying handler directly
        log_file = tmp_path / "test.log"
        logger = _build_channel("test_file", {"driver": "file", "path": str(log_file)})
        assert isinstance(logger, logging.Logger)
        assert logger.name == "hunt.test_file"
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)

    def test_daily_driver_creates_timed_handler(self, tmp_path, monkeypatch):
        import logging.handlers

        monkeypatch.setenv("LOG_NON_BLOCKING", "false")
        log_file = tmp_path / "daily.log"
        logger = _build_channel("test_daily", {"driver": "daily", "path": str(log_file)})
        assert any(isinstance(h, logging.handlers.TimedRotatingFileHandler) for h in logger.handlers)

    def test_file_driver_non_blocking_uses_queue_handler(self, tmp_path, monkeypatch):
        import logging.handlers

        from hunt.log import manager as log_manager

        monkeypatch.setenv("LOG_NON_BLOCKING", "true")
        monkeypatch.setenv("APP_ENV", "production")
        log_file = tmp_path / "test.log"
        try:
            logger = _build_channel("test_nonblocking", {"driver": "file", "path": str(log_file)})
            assert any(isinstance(h, logging.handlers.QueueHandler) for h in logger.handlers)
            # The real rotating handler is owned by a background listener.
            assert log_manager._listeners
            assert isinstance(log_manager._listeners[-1].handlers[0], logging.handlers.RotatingFileHandler)
        finally:
            log_manager._stop_listeners()

    def test_stderr_driver_creates_stream_handler(self):
        logger = _build_channel("test_stderr", {"driver": "stderr"})
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    def test_unknown_driver_falls_back_to_stderr(self):
        logger = _build_channel("test_unknown", {"driver": "nonexistent"})
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    def test_log_level_set_correctly(self, tmp_path):
        log_file = tmp_path / "test.log"
        logger = _build_channel("test_level", {"driver": "file", "path": str(log_file), "level": "warning"})
        assert logger.level == logging.WARNING

    def test_json_formatter_used_when_env_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        monkeypatch.setenv("LOG_NON_BLOCKING", "false")  # inspect the underlying handler directly
        log_file = tmp_path / "test.log"
        logger = _build_channel("test_json", {"driver": "file", "path": str(log_file)})
        for handler in logger.handlers:
            assert isinstance(handler.formatter, _JsonFormatter)

    def test_text_formatter_used_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "text")
        log_file = tmp_path / "test.log"
        logger = _build_channel("test_text", {"driver": "file", "path": str(log_file)})
        for handler in logger.handlers:
            assert not isinstance(handler.formatter, _JsonFormatter)

    def test_file_created_in_directory(self, tmp_path):
        log_file = tmp_path / "logs" / "app.log"
        _build_channel("test_create", {"driver": "file", "path": str(log_file)})
        assert log_file.parent.exists()

    def test_base_path_used_when_no_path_given(self, tmp_path):
        _build_channel("test_base", {"driver": "file"}, base_path=tmp_path)
        expected_log = tmp_path / "storage" / "logs" / "hunt.log"
        assert expected_log.parent.exists()

    def test_propagate_disabled(self, tmp_path):
        log_file = tmp_path / "test.log"
        logger = _build_channel("test_propagate", {"driver": "file", "path": str(log_file)})
        assert logger.propagate is False

    def test_handlers_cleared_on_reconfigure(self, tmp_path):
        log_file = tmp_path / "test.log"
        _build_channel("reconfig", {"driver": "file", "path": str(log_file)})
        logger2 = _build_channel("reconfig", {"driver": "file", "path": str(log_file)})
        assert len(logger2.handlers) == 1  # not doubled


# ---------------------------------------------------------------------------
# _LogManager
# ---------------------------------------------------------------------------


class TestLogManager:
    def test_configure_single_channel(self, tmp_path):
        manager = _LogManager()
        log_file = tmp_path / "app.log"
        manager.configure(log_path=str(log_file))
        # Should be able to log without error
        manager.info("test message")
        # File may or may not exist depending on whether a record was emitted

    def test_configure_multi_channel(self, tmp_path):
        manager = _LogManager()
        manager.configure(
            channels={
                "file": {"driver": "file", "path": str(tmp_path / "app.log")},
                "stderr": {"driver": "stderr"},
            },
            default="file",
        )
        manager.info("hello")

    def test_channel_returns_proxy(self, tmp_path):
        from hunt.log.manager import _ChannelProxy

        manager = _LogManager()
        manager.configure(
            channels={"stderr": {"driver": "stderr"}},
            default="stderr",
        )
        proxy = manager.channel("stderr")
        assert isinstance(proxy, _ChannelProxy)

    def test_channel_unknown_raises(self, tmp_path):
        manager = _LogManager()
        manager.configure(channels={"stderr": {"driver": "stderr"}})
        with pytest.raises(RuntimeError, match="not configured"):
            manager.channel("nonexistent")

    def test_format_with_context(self):
        result = _LogManager._format("message", {"key": "value"})
        assert "message" in result
        assert "key=" in result

    def test_format_strips_newlines_from_message(self):
        result = _LogManager._format("line1\nline2", {})
        assert "\n" not in result
        assert "\\n" in result

    def test_format_strips_carriage_returns(self):
        result = _LogManager._format("line\rend", {})
        assert "\r" not in result

    def test_debug_info_warning_error_critical_all_log(self, tmp_path):
        manager = _LogManager()
        log_file = tmp_path / "all.log"
        manager.configure(log_path=str(log_file), level="debug")
        manager.debug("d")
        manager.info("i")
        manager.warning("w")
        manager.error("e")
        manager.critical("c")
        # No exception means all calls succeeded
