"""Tests for M23 — App Introspection CLI commands."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _migrations_dir(tmp_path: Path) -> Path:
    d = tmp_path / "database" / "migrations"
    d.mkdir(parents=True)
    return d


def _write_migration(d: Path, name: str) -> None:
    (d / f"{name}.py").write_text(
        "from hunt.database.schema.migration import Migration\n"
        f"class {name.split('_', 3)[-1].title().replace('_','')}(Migration):\n"
        "    def up(self): pass\n"
        "    def down(self): pass\n"
    )


def _config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    (d / "app.py").write_text(
        'app = {"name": "TestApp", "debug": False}\n'
    )
    (d / "database.py").write_text(
        'database = {"connection": "sqlite", "password": "s3cr3t"}\n'
    )
    return d


# ---------------------------------------------------------------------------
# route:list --json
# ---------------------------------------------------------------------------

class TestRouteListJson:
    def test_json_flag_outputs_valid_json(self, tmp_path, monkeypatch):
        from hunt.console.commands.route_list import route_list_command

        # Create a minimal bootstrap/app.py stub
        bs = tmp_path / "bootstrap"
        bs.mkdir()
        (bs / "__init__.py").write_text("")
        (bs / "app.py").write_text(
            "from hunt.http.router import Router\n"
            "from hunt.application import Application\n"
            "from hunt.http.kernel import HttpKernel\n"
            "router = Router()\n"
            "router.get('/health', lambda r: 'ok').named('health')\n"
            "application = Application()\n"
            "application.instance('router', router)\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delitem(sys.modules, "bootstrap", raising=False)
        monkeypatch.delitem(sys.modules, "bootstrap.app", raising=False)
        result = CliRunner().invoke(route_list_command, ["--json"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0]["uri"] == "/health"
        assert rows[0]["name"] == "health"
        assert "method" in rows[0]
        assert "action" in rows[0]
        assert "middleware" in rows[0]

    def test_json_empty_when_no_routes(self, tmp_path, monkeypatch):
        from hunt.console.commands.route_list import route_list_command

        bs = tmp_path / "bootstrap"
        bs.mkdir()
        (bs / "__init__.py").write_text("")
        (bs / "app.py").write_text(
            "from hunt.http.router import Router\n"
            "from hunt.application import Application\n"
            "router = Router()\n"
            "application = Application()\n"
            "application.instance('router', router)\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delitem(sys.modules, "bootstrap", raising=False)
        monkeypatch.delitem(sys.modules, "bootstrap.app", raising=False)
        result = CliRunner().invoke(route_list_command, ["--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_table_output_without_flag(self, tmp_path, monkeypatch):
        from hunt.console.commands.route_list import route_list_command

        bs = tmp_path / "bootstrap"
        bs.mkdir()
        (bs / "__init__.py").write_text("")
        (bs / "app.py").write_text(
            "from hunt.http.router import Router\n"
            "from hunt.application import Application\n"
            "router = Router()\n"
            "router.get('/ping', lambda r: 'pong').named('ping')\n"
            "application = Application()\n"
            "application.instance('router', router)\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delitem(sys.modules, "bootstrap", raising=False)
        monkeypatch.delitem(sys.modules, "bootstrap.app", raising=False)
        result = CliRunner().invoke(route_list_command, [])
        assert result.exit_code == 0
        assert "/ping" in result.output
        assert "ping" in result.output


# ---------------------------------------------------------------------------
# db:status
# ---------------------------------------------------------------------------

class TestDbStatus:
    def test_table_output(self, tmp_path, monkeypatch):
        from hunt.console.commands.db.status import db_status_command

        monkeypatch.chdir(tmp_path)
        d = _migrations_dir(tmp_path)
        _write_migration(d, "2026_01_01_000001_create_users_table")
        result = CliRunner().invoke(db_status_command, [])
        assert result.exit_code == 0, result.output
        assert "create_users_table" in result.output
        assert "Pending" in result.output or "Ran" in result.output

    def test_json_output(self, tmp_path, monkeypatch):
        from hunt.console.commands.db.status import db_status_command

        monkeypatch.chdir(tmp_path)
        d = _migrations_dir(tmp_path)
        _write_migration(d, "2026_01_01_000001_create_posts_table")
        _write_migration(d, "2026_01_01_000002_create_tags_table")
        result = CliRunner().invoke(db_status_command, ["--json"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert isinstance(rows, list)
        assert len(rows) == 2
        assert all("migration" in r and "ran" in r for r in rows)

    def test_empty_migrations_dir(self, tmp_path, monkeypatch):
        from hunt.console.commands.db.status import db_status_command

        monkeypatch.chdir(tmp_path)
        _migrations_dir(tmp_path)
        result = CliRunner().invoke(db_status_command, [])
        assert result.exit_code == 0
        assert "No migrations found" in result.output

    def test_empty_json(self, tmp_path, monkeypatch):
        from hunt.console.commands.db.status import db_status_command

        monkeypatch.chdir(tmp_path)
        _migrations_dir(tmp_path)
        result = CliRunner().invoke(db_status_command, ["--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == []


# ---------------------------------------------------------------------------
# config:show
# ---------------------------------------------------------------------------

class TestConfigShow:
    def test_table_output(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        _config_dir(tmp_path)
        result = CliRunner().invoke(config_show_command, [])
        assert result.exit_code == 0, result.output
        assert "TestApp" in result.output

    def test_sensitive_values_redacted(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        _config_dir(tmp_path)
        result = CliRunner().invoke(config_show_command, [])
        assert result.exit_code == 0
        assert "s3cr3t" not in result.output
        assert "redacted" in result.output

    def test_no_redact_flag_shows_secrets(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        _config_dir(tmp_path)
        result = CliRunner().invoke(config_show_command, ["--no-redact"])
        assert result.exit_code == 0
        assert "s3cr3t" in result.output

    def test_json_output(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        _config_dir(tmp_path)
        result = CliRunner().invoke(config_show_command, ["--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_key_filter(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        _config_dir(tmp_path)
        result = CliRunner().invoke(config_show_command, ["app"])
        assert result.exit_code == 0
        assert "TestApp" in result.output

    def test_unknown_key_exits(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        _config_dir(tmp_path)
        result = CliRunner().invoke(config_show_command, ["does_not_exist"])
        assert result.exit_code != 0

    def test_no_config_dir(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import config_show_command

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(config_show_command, [])
        assert result.exit_code == 0
        assert "No configuration found" in result.output

    def test_redact_detects_key_in_name(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import _redact_dict

        data = {"api_key": "my-secret-key", "name": "public"}
        result = _redact_dict(data)
        assert result["api_key"] == "*** redacted ***"
        assert result["name"] == "public"

    def test_redact_nested(self, tmp_path, monkeypatch):
        from hunt.console.commands.config_show import _redact_dict

        data = {"database": {"password": "hunter2", "host": "localhost"}}
        result = _redact_dict(data)
        assert result["database"]["password"] == "*** redacted ***"
        assert result["database"]["host"] == "localhost"


# ---------------------------------------------------------------------------
# app:info
# ---------------------------------------------------------------------------

class TestAppInfo:
    def _make_project(self, tmp_path: Path) -> None:
        for d in ("app/models", "app/controllers", "app/middleware", "app/providers", "app/jobs"):
            (tmp_path / d).mkdir(parents=True)
        (tmp_path / "app" / "models" / "post.py").write_text("class Post: pass\n")
        (tmp_path / "app" / "models" / "user.py").write_text("class User: pass\n")
        _migrations_dir(tmp_path)
        _write_migration(tmp_path / "database" / "migrations", "2026_01_01_000001_create_users_table")

    def test_table_output(self, tmp_path, monkeypatch):
        from hunt.console.commands.app_info import app_info_command

        self._make_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_NAME", "MyApp")
        monkeypatch.setenv("APP_ENV", "testing")
        result = CliRunner().invoke(app_info_command, [])
        assert result.exit_code == 0, result.output
        assert "MyApp" in result.output
        assert "testing" in result.output

    def test_json_output(self, tmp_path, monkeypatch):
        from hunt.console.commands.app_info import app_info_command

        self._make_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("APP_NAME", "MyApp")
        result = CliRunner().invoke(app_info_command, ["--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["app_name"] == "MyApp"
        assert "framework_version" in data
        assert "counts" in data
        assert data["counts"]["models"] == 2
        assert "migrations" in data["counts"]
        assert data["counts"]["migrations"]["total"] == 1

    def test_json_has_drivers(self, tmp_path, monkeypatch):
        from hunt.console.commands.app_info import app_info_command

        self._make_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app_info_command, ["--json"])
        data = json.loads(result.output)
        assert "drivers" in data
        assert "database" in data["drivers"]
        assert "session" in data["drivers"]

    def test_framework_version_present(self, tmp_path, monkeypatch):
        from hunt import __version__
        from hunt.console.commands.app_info import app_info_command

        self._make_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app_info_command, ["--json"])
        data = json.loads(result.output)
        assert data["framework_version"] == __version__
