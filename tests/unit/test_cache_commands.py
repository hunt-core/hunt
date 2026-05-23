"""Tests for the cache CLI commands."""
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hunt.console.commands.cache import cache_clear, cache_forget


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# cache clear
# ---------------------------------------------------------------------------

class TestCacheClear:
    def test_clears_cache_directory(self, project):
        cache_dir = project / "storage" / "framework" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "old_entry.json").write_text("{}")

        result = CliRunner().invoke(cache_clear, [])

        assert result.exit_code == 0
        assert cache_dir.exists()
        assert not list(cache_dir.iterdir())

    def test_clears_views_cache_directory(self, project):
        views_dir = project / "storage" / "framework" / "views"
        views_dir.mkdir(parents=True)
        (views_dir / "cached.html").write_text("<p>old</p>")

        CliRunner().invoke(cache_clear, [])

        assert views_dir.exists()
        assert not list(views_dir.iterdir())

    def test_succeeds_when_directories_do_not_exist(self, project):
        result = CliRunner().invoke(cache_clear, [])
        assert result.exit_code == 0
        assert "Cache cleared" in result.output

    def test_recreates_directories_after_clearing(self, project):
        cache_dir = project / "storage" / "framework" / "cache"
        views_dir = project / "storage" / "framework" / "views"
        cache_dir.mkdir(parents=True)
        views_dir.mkdir(parents=True)

        CliRunner().invoke(cache_clear, [])

        assert cache_dir.exists()
        assert views_dir.exists()

    def test_prints_confirmation(self, project):
        result = CliRunner().invoke(cache_clear, [])
        assert "Cache cleared" in result.output


# ---------------------------------------------------------------------------
# cache forget
# ---------------------------------------------------------------------------

class TestCacheForget:
    def test_calls_cache_forget_with_key(self, project):
        with patch("hunt.cache.manager.Cache") as mock_cache:
            result = CliRunner().invoke(cache_forget, ["my-key"])

        assert result.exit_code == 0
        mock_cache.forget.assert_called_once_with("my-key")

    def test_prints_removed_key(self, project):
        with patch("hunt.cache.manager.Cache"):
            result = CliRunner().invoke(cache_forget, ["session:abc123"])

        assert "session:abc123" in result.output
