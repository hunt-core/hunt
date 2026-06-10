"""Tests for Application._configure_managers — config/*.py is the source of truth."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hunt.application import Application


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "config").mkdir()
    return tmp_path


def _boot(project):
    """Construct an Application with all managers patched out; return the mocks."""
    with (
        patch("hunt.mail.manager.Mail") as mail,
        patch("hunt.cache.manager.Cache") as cache,
        patch("hunt.queue.manager.Queue") as queue,
        patch("hunt.log.manager.Log") as log,
        patch("hunt.storage.manager.Storage") as storage,
    ):
        Application(project)
    return {"mail": mail, "cache": cache, "queue": queue, "log": log, "storage": storage}


class TestConfigureManagers:
    def test_cache_section_forwarded_with_resolved_path(self, project):
        (project / "config" / "cache.py").write_text(
            'config = {"driver": "file", "path": "storage/framework/cache", "prefix": "x:"}\n'
        )
        mocks = _boot(project)
        mocks["cache"].configure.assert_called_once_with(
            driver="file", path=project / "storage/framework/cache", prefix="x:"
        )

    def test_cache_absolute_path_kept(self, project, tmp_path):
        (project / "config" / "cache.py").write_text(f'config = {{"driver": "file", "path": r"{tmp_path}"}}\n')
        mocks = _boot(project)
        assert mocks["cache"].configure.call_args.kwargs["path"] == str(tmp_path)

    def test_cache_unknown_keys_filtered(self, project):
        (project / "config" / "cache.py").write_text('config = {"driver": "array", "bogus": 1}\n')
        mocks = _boot(project)
        mocks["cache"].configure.assert_called_once_with(driver="array")

    def test_queue_section_forwarded(self, project):
        (project / "config" / "queue.py").write_text(
            'config = {"driver": "redis", "host": "r.example.com", "port": 6380}\n'
        )
        mocks = _boot(project)
        mocks["queue"].configure.assert_called_once_with("redis", host="r.example.com", port=6380)

    def test_logging_section_forwarded_with_base_path(self, project):
        (project / "config" / "logging.py").write_text(
            'config = {"default": "stderr", "channels": {"stderr": {"driver": "stderr"}}}\n'
        )
        mocks = _boot(project)
        mocks["log"].configure.assert_called_once_with(
            channels={"stderr": {"driver": "stderr"}},
            default="stderr",
            base_path=project,
        )

    def test_filesystems_section_forwarded_to_storage(self, project):
        (project / "config" / "filesystems.py").write_text(
            'config = {"default": "local", "disks": {"local": {"driver": "local", "root": "/tmp/x"}}}\n'
        )
        mocks = _boot(project)
        mocks["storage"].configure.assert_called_once_with(
            {"default": "local", "disks": {"local": {"driver": "local", "root": "/tmp/x"}}}
        )

    def test_missing_sections_configure_nothing(self, project):
        mocks = _boot(project)
        for mock in mocks.values():
            mock.configure.assert_not_called()


class TestDatabaseAndViewWiring:
    def test_database_section_forwarded_with_resolved_sqlite_path(self, project):
        (project / "config" / "database.py").write_text(
            'config = {"default": "sqlite", "connections": {'
            '"sqlite": {"driver": "sqlite", "database": "database/db.sqlite"}}}\n'
        )
        with patch("hunt.database.connection.configure") as db_configure:
            Application(project)
        cfg = db_configure.call_args.args[0]
        assert cfg["default"] == "sqlite"
        assert cfg["connections"]["sqlite"]["database"] == str(project / "database/db.sqlite")

    def test_database_memory_path_untouched(self, project):
        (project / "config" / "database.py").write_text(
            'config = {"default": "sqlite", "connections": {'
            '"sqlite": {"driver": "sqlite", "database": ":memory:"}}}\n'
        )
        with patch("hunt.database.connection.configure") as db_configure:
            Application(project)
        assert db_configure.call_args.args[0]["connections"]["sqlite"]["database"] == ":memory:"

    def test_view_section_binds_view_factory(self, project):
        (project / "config" / "view.py").write_text(
            'config = {"paths": ["resources/views"], "cache": "storage/framework/views", "extension": ".html"}\n'
        )
        app = Application(project)
        factory = app.make("view")
        assert factory._views_path == project / "resources/views"
        assert factory._extension == ".html"


class TestConnectionConfig:
    def setup_method(self):
        import importlib

        conn = importlib.import_module("hunt.database.connection")

        self._saved = (dict(conn._connections), dict(conn._config))
        conn._connections.clear()
        conn._config = {}

    def teardown_method(self):
        import importlib

        conn = importlib.import_module("hunt.database.connection")

        conn._connections.clear()
        conn._connections.update(self._saved[0])
        conn._config = self._saved[1]

    def test_engine_built_from_config(self, monkeypatch, tmp_path):
        import importlib

        conn = importlib.import_module("hunt.database.connection")

        monkeypatch.delenv("DATABASE_URL", raising=False)
        db_file = tmp_path / "cfg.sqlite"
        conn.configure({"default": "sqlite", "connections": {"sqlite": {"driver": "sqlite", "database": str(db_file)}}})
        engine = conn.connection()
        assert str(db_file) in str(engine.url)

    def test_env_fallback_when_unconfigured(self, monkeypatch):
        import importlib

        conn = importlib.import_module("hunt.database.connection")

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_CONNECTION", "sqlite")
        monkeypatch.setenv("DB_DATABASE", ":memory:")
        engine = conn.connection()
        assert "sqlite" in str(engine.url)

    def test_named_connection_uses_its_own_config(self, monkeypatch):
        import importlib

        conn = importlib.import_module("hunt.database.connection")

        monkeypatch.delenv("DATABASE_URL", raising=False)
        conn.configure(
            {
                "default": "sqlite",
                "connections": {
                    "sqlite": {"driver": "sqlite", "database": ":memory:"},
                    "mysql": {"driver": "mysql", "host": "db.example.com", "database": "app"},
                },
            }
        )
        engine = conn.connection("mysql")
        assert "mysql" in str(engine.url)
        assert "db.example.com" in str(engine.url)


class TestSessionDriverResolution:
    def test_config_wins_over_env(self, monkeypatch):
        from hunt.session import session_driver

        monkeypatch.setenv("SESSION_DRIVER", "file")
        with patch("hunt.support.helpers.config", return_value="Redis"):
            assert session_driver() == "redis"

    def test_env_fallback_when_no_config(self, monkeypatch):
        from hunt.session import session_driver

        monkeypatch.setenv("SESSION_DRIVER", "REDIS")
        with patch("hunt.support.helpers.config", return_value=None):
            assert session_driver() == "redis"

    def test_default_is_file(self, monkeypatch):
        from hunt.session import session_driver

        monkeypatch.delenv("SESSION_DRIVER", raising=False)
        with patch("hunt.support.helpers.config", return_value=None):
            assert session_driver() == "file"
