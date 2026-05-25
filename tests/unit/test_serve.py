"""Tests for M25 — Live Reload Dev Server."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from hunt.console.commands.serve import serve_command


def _invoke(tmp_path: Path, *args: str) -> tuple:
    with patch("uvicorn.run") as mock_run:
        mock_run.return_value = None
        result = CliRunner().invoke(serve_command, list(args), catch_exceptions=False)
        return result, mock_run


class TestServeReload:
    def test_reload_dirs_passed_when_reload_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app").mkdir()
        (tmp_path / "routes").mkdir()
        result, mock_run = _invoke(tmp_path, "--reload")
        assert result.exit_code == 0, result.output
        kw = mock_run.call_args.kwargs
        assert kw["reload"] is True
        assert kw["reload_dirs"] is not None
        assert any("app" in d for d in kw["reload_dirs"])
        assert any("routes" in d for d in kw["reload_dirs"])

    def test_reload_dirs_only_include_existing_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app").mkdir()
        _, mock_run = _invoke(tmp_path, "--reload")
        dirs = mock_run.call_args.kwargs["reload_dirs"]
        assert all(Path(d).exists() for d in dirs)
        assert not any("resources" in d for d in dirs)

    def test_no_reload_passes_none_reload_dirs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result, mock_run = _invoke(tmp_path, "--no-reload")
        assert result.exit_code == 0
        kw = mock_run.call_args.kwargs
        assert kw["reload"] is False
        assert kw["reload_dirs"] is None

    def test_reload_and_port_compose(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "app").mkdir()
        _, mock_run = _invoke(tmp_path, "--reload", "--port=9000")
        kw = mock_run.call_args.kwargs
        assert kw["reload"] is True
        assert kw["port"] == 9000

    def test_default_reload_is_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _, mock_run = _invoke(tmp_path)
        assert mock_run.call_args.kwargs["reload"] is True


class TestServeDebugWatchMessage:
    def test_watch_message_shown_when_debug_and_reload(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_DEBUG", "true")
        (tmp_path / "app").mkdir()
        (tmp_path / "routes").mkdir()
        result, _ = _invoke(tmp_path, "--reload")
        assert result.exit_code == 0
        assert "Watching for changes" in result.output
        assert "app/" in result.output

    def test_watch_message_not_shown_when_debug_false(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_DEBUG", "false")
        (tmp_path / "app").mkdir()
        result, _ = _invoke(tmp_path, "--reload")
        assert "Watching for changes" not in result.output

    def test_watch_message_not_shown_when_no_reload(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_DEBUG", "true")
        (tmp_path / "app").mkdir()
        result, _ = _invoke(tmp_path, "--no-reload")
        assert "Watching for changes" not in result.output

    def test_watch_message_lists_existing_dirs_only(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_DEBUG", "true")
        (tmp_path / "config").mkdir()
        # app/, resources/, routes/ do NOT exist
        result, _ = _invoke(tmp_path, "--reload")
        assert "config/" in result.output
        assert "app/" not in result.output


class TestServeOpenBrowser:
    def test_open_flag_triggers_browser(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("uvicorn.run"), patch("webbrowser.open") as mock_open, patch("time.sleep"):
            CliRunner().invoke(serve_command, ["--open"], catch_exceptions=False)
            time.sleep(0.1)
            mock_open.assert_called_once()
            assert "http://127.0.0.1:8000" in mock_open.call_args.args[0]

    def test_open_uses_configured_port(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("uvicorn.run"), patch("webbrowser.open") as mock_open, patch("time.sleep"):
            CliRunner().invoke(serve_command, ["--open", "--port=9090"], catch_exceptions=False)
            time.sleep(0.1)
            mock_open.assert_called_once()
            assert "9090" in mock_open.call_args.args[0]

    def test_no_open_flag_does_not_open_browser(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("uvicorn.run"), patch("webbrowser.open") as mock_open:
            CliRunner().invoke(serve_command, [], catch_exceptions=False)
            time.sleep(0.1)
            mock_open.assert_not_called()


class TestServeOutput:
    def test_url_in_startup_message(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result, _ = _invoke(tmp_path)
        assert "http://127.0.0.1:8000" in result.output

    def test_ctrl_c_hint_in_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result, _ = _invoke(tmp_path)
        assert "Ctrl+C" in result.output
