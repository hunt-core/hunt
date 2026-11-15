"""Regression tests for CLI-02, CLI-L4, CLI-L5, GEN-11 and GEN-08 (serve:production options)."""

from __future__ import annotations

import os
import stat
import sys
from unittest.mock import patch

import pytest
from click.testing import CliRunner


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# CLI-02: key:generate
# ---------------------------------------------------------------------------


class TestKeyGenerate:
    def _run(self, args=()):
        from hunt.console.commands.key_generate import key_generate_command

        return CliRunner().invoke(key_generate_command, list(args))

    def test_only_the_app_key_line_is_rewritten(self, project):
        env = project / ".env"
        env.write_text("STRIPE_APP_KEY=sk_live_1\nOLD_APP_KEY=old\n# APP_KEY=commented\nAPP_KEY=\nOTHER=APP_KEY=x\n")

        result = self._run()

        assert result.exit_code == 0
        lines = env.read_text().splitlines()
        assert lines[0] == "STRIPE_APP_KEY=sk_live_1"
        assert lines[1] == "OLD_APP_KEY=old"
        assert lines[2] == "# APP_KEY=commented"
        assert lines[3].startswith("APP_KEY=base64:")
        assert lines[4] == "OTHER=APP_KEY=x"

    def test_only_first_app_key_line_is_replaced(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=\nAPP_KEY=\n")
        self._run()
        lines = env.read_text().splitlines()
        assert lines[0].startswith("APP_KEY=base64:")
        assert lines[1] == "APP_KEY="

    def test_key_is_not_printed(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=\n")
        result = self._run()
        key = env.read_text().strip().split("=", 1)[1]
        assert key.startswith("base64:")
        assert key not in result.output
        assert "base64:" not in result.output
        assert "Application key set." in result.output

    def test_second_run_without_force_exits_1_and_keeps_key(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=\n")
        assert self._run().exit_code == 0
        first = env.read_text()

        result = self._run()

        assert result.exit_code == 1
        assert "--force" in result.output
        assert env.read_text() == first

    def test_force_rotates_existing_key(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=base64:old\nX=1\n")
        result = self._run(["--force"])
        assert result.exit_code == 0
        content = env.read_text()
        assert "base64:old" not in content
        assert content.endswith("X=1\n")

    def test_interactive_confirmation_declined_keeps_key(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=base64:old\n")
        with (
            patch("hunt.console.commands.key_generate._is_interactive", return_value=True),
            patch("hunt.console.commands.key_generate.click.confirm", return_value=False) as confirm,
        ):
            result = self._run()
        confirm.assert_called_once()
        assert result.exit_code == 1
        assert env.read_text() == "APP_KEY=base64:old\n"

    def test_interactive_confirmation_accepted_rotates(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=base64:old\n")
        with (
            patch("hunt.console.commands.key_generate._is_interactive", return_value=True),
            patch("hunt.console.commands.key_generate.click.confirm", return_value=True),
        ):
            result = self._run()
        assert result.exit_code == 0
        assert "base64:old" not in env.read_text()

    def test_written_with_mode_0600_and_no_temp_file_left(self, project):
        env = project / ".env"
        env.write_text("APP_KEY=\n")
        env.chmod(0o644)
        self._run()
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
        assert [p.name for p in project.iterdir()] == [".env"]

    def test_appended_key_starts_on_its_own_line(self, project):
        env = project / ".env"
        env.write_text("APP_NAME=x")
        self._run()
        lines = env.read_text().splitlines()
        assert lines[0] == "APP_NAME=x"
        assert lines[1].startswith("APP_KEY=base64:")


# ---------------------------------------------------------------------------
# CLI-L4: env:check and config:show do not print credentials
# ---------------------------------------------------------------------------


class TestSecretsNotPrinted:
    def test_env_check_prints_set_instead_of_values(self, project, monkeypatch):
        from hunt.console.commands.env_check import env_check_command

        monkeypatch.setenv("APP_KEY", "base64:abc")
        monkeypatch.setenv("DB_CONNECTION", "sqlite")
        monkeypatch.setenv("REDIS_URL", "redis://default:hunter2@cache.internal:6379/0")
        monkeypatch.setenv("SENTRY_DSN", "https://publickey123@o1.ingest.sentry.io/42")

        result = CliRunner().invoke(env_check_command, [])

        assert result.exit_code == 0
        assert "hunter2" not in result.output
        assert "cache.internal" not in result.output
        assert "publickey123" not in result.output
        assert "REDIS_URL  (set)" in result.output
        assert "SENTRY_DSN  (set)" in result.output

    def test_url_password_masked_under_any_key(self):
        from hunt.console.commands.config_show import _redact_dict

        data = {"broker": {"connection": "amqp://guest:s3cret@mq:5672/vhost", "hosts": ["redis://u:pw@h:1"]}}
        out = _redact_dict(data)
        assert out["broker"]["connection"] == "amqp://guest:***@mq:5672/vhost"
        assert out["broker"]["hosts"] == ["redis://u:***@h:1"]

    @pytest.mark.parametrize("key", ["url", "dsn", "uri", "database_url", "redis.url", "sentry-dsn"])
    def test_url_dsn_uri_keys_with_credentials_are_redacted(self, key):
        from hunt.console.commands.config_show import _redact_dict

        out = _redact_dict({key: "https://publickey@sentry.example/1"})
        assert out[key] == "*** redacted ***"

    def test_plain_url_without_credentials_is_shown(self):
        from hunt.console.commands.config_show import _redact_dict

        out = _redact_dict({"app": {"url": "http://localhost:8000", "security": "strict"}})
        assert out["app"]["url"] == "http://localhost:8000"
        assert out["app"]["security"] == "strict"

    def test_config_show_command_masks_urls(self, project):
        from hunt.console.commands.config_show import config_show_command

        (project / "config").mkdir()
        (project / "config" / "database.py").write_text(
            'config = {"url": "postgres://app:topsecret@db:5432/app", "replica": "mysql://ro:alsosecret@r/app"}\n'
        )
        result = CliRunner().invoke(config_show_command, [])
        assert result.exit_code == 0
        assert "topsecret" not in result.output
        assert "alsosecret" not in result.output
        assert "mysql://ro:***@r/app" in result.output

    def test_config_show_narrowed_key_is_still_redacted(self, project):
        from hunt.console.commands.config_show import config_show_command

        (project / "config").mkdir()
        (project / "config" / "database.py").write_text('config = {"password": "topsecret"}\n')
        result = CliRunner().invoke(config_show_command, ["database.password"])
        assert "topsecret" not in result.output


# ---------------------------------------------------------------------------
# CLI-L5: cache commands, context, console kernel
# ---------------------------------------------------------------------------


class TestCacheCommandsBootTheApp:
    def test_forget_refuses_outside_an_application(self, project):
        from hunt.console.commands.cache import cache_forget

        with patch("hunt.cache.manager.Cache") as cache:
            result = CliRunner().invoke(cache_forget, ["k"])
        assert result.exit_code == 1
        assert "Removed" not in result.output
        cache.forget.assert_not_called()

    def test_forget_acts_on_the_store_configured_by_the_application(self, project, monkeypatch):
        from hunt.cache.manager import ArrayStore, Cache
        from hunt.console.commands.cache import cache_forget

        (project / "bootstrap").mkdir()
        (project / "bootstrap" / "__init__.py").write_text("")
        (project / "bootstrap" / "app.py").write_text(
            "from hunt.cache.manager import Cache\nCache.configure('file', path=__import__('pathlib').Path('fc'))\n"
            "Cache.put('k', 'v')\nCache.put('other', 'v')\n"
        )
        monkeypatch.syspath_prepend(str(project))
        for name in ("bootstrap", "bootstrap.app"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setattr(Cache, "_store", ArrayStore(), raising=False)

        result = CliRunner().invoke(cache_forget, ["k"])

        try:
            assert result.exit_code == 0, result.output
            assert "Removed: k" in result.output
            assert Cache.get("k") is None
            assert Cache.get("other") == "v"
        finally:
            for name in ("bootstrap", "bootstrap.app"):
                sys.modules.pop(name, None)

    def test_forget_reports_boot_failure(self, project):
        from hunt.console.commands.cache import cache_forget

        with patch("hunt.console.commands.cache._boot_application", side_effect=RuntimeError("boom")):
            result = CliRunner().invoke(cache_forget, ["k"])
        assert result.exit_code == 1
        assert "boom" in result.output
        assert "Removed" not in result.output

    def test_clear_flushes_the_configured_store(self, project):
        from hunt.console.commands.cache import cache_clear

        with (
            patch("hunt.console.commands.cache._boot_application", return_value=True),
            patch("hunt.cache.manager.Cache") as cache,
        ):
            result = CliRunner().invoke(cache_clear, [])
        assert result.exit_code == 0
        cache.flush.assert_called_once_with()

    def test_clear_flushes_redis_keys(self, project, monkeypatch):
        from hunt.cache.manager import Cache, RedisStore
        from hunt.console.commands.cache import cache_clear

        class FakeRedis:
            def __init__(self):
                self.data = {b"hunt_cache:a": b"1", b"hunt_cache:b": b"2", b"unrelated": b"3"}

            def scan(self, cursor, match=None, count=None):
                prefix = match.rstrip("*").encode()
                return 0, [k for k in self.data if k.startswith(prefix)]

            def delete(self, *keys):
                for k in keys:
                    self.data.pop(k, None)

        fake = FakeRedis()
        store = RedisStore.__new__(RedisStore)
        store._redis = fake
        store._prefix = "hunt_cache:"
        monkeypatch.setattr(Cache, "_store", store, raising=False)

        with patch("hunt.console.commands.cache._boot_application", return_value=True):
            result = CliRunner().invoke(cache_clear, [])

        assert result.exit_code == 0
        assert fake.data == {b"unrelated": b"3"}

    def test_clear_fails_when_store_cannot_be_flushed(self, project):
        from hunt.console.commands.cache import cache_clear

        with (
            patch("hunt.console.commands.cache._boot_application", return_value=True),
            patch("hunt.cache.manager.Cache") as cache,
        ):
            cache.flush.side_effect = ConnectionError("redis down")
            result = CliRunner().invoke(cache_clear, [])
        assert result.exit_code == 1
        assert "Cache cleared" not in result.output


class TestContextMiddlewareDirectory:
    def test_scans_app_middleware(self, project):
        from hunt.console.commands.context import _discover_middleware

        mw = project / "app" / "middleware"
        mw.mkdir(parents=True)
        (mw / "guest.py").write_text("class RedirectIfAuthenticated:\n    pass\n")

        found = _discover_middleware(project)

        assert found == [{"class": "RedirectIfAuthenticated", "file": os.path.join("app", "middleware", "guest.py")}]


class TestConsoleKernelAppCommands:
    _KERNEL = "from pathlib import Path\nPath('executed.txt').write_text('x')\ndef register(cli):\n    pass\n"

    def _make(self, project, with_bootstrap):
        (project / "app" / "console").mkdir(parents=True)
        (project / "app" / "console" / "kernel.py").write_text(self._KERNEL)
        if with_bootstrap:
            (project / "bootstrap").mkdir()
            (project / "bootstrap" / "app.py").write_text("")

    def _load(self, project, monkeypatch, argv):
        import click

        from hunt.console.kernel import _load_app_commands

        monkeypatch.setattr(sys, "path", list(sys.path))
        _load_app_commands(click.Group("t"), argv)
        return (project / "executed.txt").exists()

    def test_not_executed_outside_a_hunt_app(self, project, monkeypatch):
        self._make(project, with_bootstrap=False)
        assert self._load(project, monkeypatch, ["route:list"]) is False

    def test_not_executed_for_new(self, project, monkeypatch):
        self._make(project, with_bootstrap=True)
        assert self._load(project, monkeypatch, ["new", "blog"]) is False

    def test_executed_inside_a_hunt_app(self, project, monkeypatch):
        self._make(project, with_bootstrap=True)
        assert self._load(project, monkeypatch, ["route:list"]) is True

    def test_argv_defaults_to_process_arguments(self, project, monkeypatch):
        self._make(project, with_bootstrap=True)
        monkeypatch.setattr(sys, "argv", ["hunt", "new", "x"])
        assert self._load(project, monkeypatch, None) is False


# ---------------------------------------------------------------------------
# GEN-11 / GEN-08: env requirements and serve:production
# ---------------------------------------------------------------------------

_ENV_KEYS = ("APP_KEY", "DATABASE_URL", "DB_CONNECTION", "DB_HOST", "APP_ENV", "APP_DEBUG")


@pytest.fixture()
def clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class TestRequiredEnvironment:
    def test_db_connection_satisfies_the_database_requirement(self, project, clean_env):
        from hunt.console.commands.env_check import check_required

        clean_env.setenv("APP_KEY", "base64:x")
        clean_env.setenv("DB_CONNECTION", "sqlite")
        assert check_required() == []

    def test_database_url_satisfies_the_database_requirement(self, project, clean_env):
        from hunt.console.commands.env_check import check_required

        clean_env.setenv("APP_KEY", "base64:x")
        clean_env.setenv("DATABASE_URL", "sqlite:///x.db")
        assert check_required() == []

    def test_missing_database_is_reported_with_both_names(self, project, clean_env):
        from hunt.console.commands.env_check import check_required

        clean_env.setenv("APP_KEY", "base64:x")
        assert check_required() == ["DATABASE_URL or DB_CONNECTION"]

    def test_scaffolded_env_passes_check_required(self, tmp_path, monkeypatch):
        from dotenv import dotenv_values

        from hunt.console.commands.env_check import check_required
        from hunt.console.commands.new import new_command

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(new_command, ["demo"])
        assert result.exit_code == 0, result.output
        app_dir = tmp_path / "demo"
        # check_required() loads .env into os.environ. Register every key for
        # restoration first: delenv() alone records nothing for a key that is
        # not set, so set it and then delete it.
        for key in set(dotenv_values(app_dir / ".env")) | set(_ENV_KEYS):
            monkeypatch.setenv(key, "")
            monkeypatch.delenv(key)
        monkeypatch.chdir(app_dir)

        assert check_required() == []


class TestServeProduction:
    def _invoke(self, args=()):
        from hunt.console.commands.serve_production import serve_production_command

        with patch("uvicorn.run") as run:
            result = CliRunner().invoke(serve_production_command, ["--workers", "1", *args])
        return result, run

    def _env(self, clean_env, debug="false", env="production"):
        clean_env.setenv("APP_KEY", "base64:x")
        clean_env.setenv("DB_CONNECTION", "sqlite")
        clean_env.setenv("APP_ENV", env)
        clean_env.setenv("APP_DEBUG", debug)

    def test_starts_with_db_connection_only(self, project, clean_env):
        self._env(clean_env)
        result, run = self._invoke()
        assert result.exit_code == 0, result.output
        run.assert_called_once()
        assert "No database configured" not in result.output

    def test_refuses_without_database_settings(self, project, clean_env):
        clean_env.setenv("APP_KEY", "base64:x")
        result, run = self._invoke()
        assert result.exit_code == 1
        assert "DATABASE_URL or DB_CONNECTION" in result.output
        run.assert_not_called()

    @pytest.mark.parametrize("value", ["true", "True", "1"])
    def test_refuses_debug_in_production(self, project, clean_env, value):
        self._env(clean_env, debug=value)
        result, run = self._invoke()
        assert result.exit_code == 1
        assert "--allow-debug" in result.output
        run.assert_not_called()

    def test_allow_debug_starts(self, project, clean_env):
        self._env(clean_env, debug="true")
        result, run = self._invoke(["--allow-debug"])
        assert result.exit_code == 0, result.output
        run.assert_called_once()

    def test_debug_outside_production_is_only_a_warning(self, project, clean_env):
        self._env(clean_env, debug="true", env="staging")
        result, run = self._invoke()
        assert result.exit_code == 0, result.output
        run.assert_called_once()

    def test_forwarded_headers_not_trusted_by_default(self, project, clean_env):
        self._env(clean_env)
        _, run = self._invoke()
        kwargs = run.call_args.kwargs
        assert kwargs["proxy_headers"] is False
        assert kwargs["forwarded_allow_ips"] is None

    def test_forwarded_allow_ips_enables_proxy_headers(self, project, clean_env):
        self._env(clean_env)
        _, run = self._invoke(["--forwarded-allow-ips", "10.0.0.5"])
        kwargs = run.call_args.kwargs
        assert kwargs["proxy_headers"] is True
        assert kwargs["forwarded_allow_ips"] == "10.0.0.5"

    def test_proxy_headers_alone_trusts_loopback_only(self, project, clean_env):
        self._env(clean_env)
        _, run = self._invoke(["--proxy-headers"])
        kwargs = run.call_args.kwargs
        assert kwargs["proxy_headers"] is True
        assert kwargs["forwarded_allow_ips"] == "127.0.0.1"

    def test_no_proxy_headers_wins_over_allow_ips(self, project, clean_env):
        self._env(clean_env)
        _, run = self._invoke(["--no-proxy-headers", "--forwarded-allow-ips", "*"])
        kwargs = run.call_args.kwargs
        assert kwargs["proxy_headers"] is False
        assert kwargs["forwarded_allow_ips"] is None

    def test_help_mentions_trust_proxies(self):
        from hunt.console.commands.serve_production import serve_production_command

        result = CliRunner().invoke(serve_production_command, ["--help"])
        assert "TrustProxies" in result.output
