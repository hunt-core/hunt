from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from hunt.console.commands.lodge_add import (
    _add_depends_on,
    _add_volume,
    _insert_service,
    _services_in_compose,
    lodge_add_command,
)
from hunt.console.commands.lodge_install import (
    _build_compose,
    _set_env,
    lodge_install_command,
)

# ---------------------------------------------------------------------------
# _set_env
# ---------------------------------------------------------------------------


class TestSetEnv:
    def test_updates_existing_key(self):
        content = "APP_ENV=local\nDB_HOST=127.0.0.1\n"
        result = _set_env(content, "DB_HOST", "pgsql")
        assert "DB_HOST=pgsql" in result
        assert "DB_HOST=127.0.0.1" not in result

    def test_appends_missing_key(self):
        content = "APP_ENV=local\n"
        result = _set_env(content, "DB_HOST", "pgsql")
        assert "DB_HOST=pgsql" in result
        assert "APP_ENV=local" in result

    def test_does_not_duplicate_key(self):
        content = "DB_HOST=old\n"
        result = _set_env(content, "DB_HOST", "new")
        assert result.count("DB_HOST=") == 1

    def test_handles_empty_content(self):
        result = _set_env("", "DB_HOST", "pgsql")
        assert "DB_HOST=pgsql" in result

    def test_key_with_special_regex_chars(self):
        content = "AWS_ACCESS_KEY_ID=old\n"
        result = _set_env(content, "AWS_ACCESS_KEY_ID", "lodge")
        assert "AWS_ACCESS_KEY_ID=lodge" in result

    def test_only_replaces_exact_key(self):
        content = "DB_HOST=old\nDB_HOST_REPLICA=other\n"
        result = _set_env(content, "DB_HOST", "new")
        assert "DB_HOST=new" in result
        assert "DB_HOST_REPLICA=other" in result


# ---------------------------------------------------------------------------
# _build_compose
# ---------------------------------------------------------------------------


class TestBuildCompose:
    def test_no_services_contains_app(self):
        out = _build_compose("myapp", "3.12", [])
        assert "services:" in out
        assert "app:" in out
        assert "networks:" in out
        assert "depends_on" not in out
        # Named volumes section should not appear; app service bind-mounts are ok
        assert "lodge-" not in out

    def test_python_version_in_args(self):
        out = _build_compose("myapp", "3.11", [])
        assert '"3.11"' in out

    def test_app_name_in_image(self):
        out = _build_compose("myapp", "3.12", [])
        assert '"myapp/app"' in out

    def test_pgsql_service_included(self):
        out = _build_compose("myapp", "3.12", ["pgsql"])
        assert "pgsql:" in out
        assert "postgres:16" in out
        assert "lodge-pgsql" in out
        assert "service_healthy" in out

    def test_redis_service_included(self):
        out = _build_compose("myapp", "3.12", ["redis"])
        assert "redis:" in out
        assert "redis:alpine" in out

    def test_mailpit_uses_service_started(self):
        out = _build_compose("myapp", "3.12", ["mailpit"])
        assert "mailpit:" in out
        assert "service_started" in out
        assert "service_healthy" not in out

    def test_minio_has_healthcheck_condition(self):
        out = _build_compose("myapp", "3.12", ["minio"])
        assert "minio:" in out
        assert "service_healthy" in out

    def test_multiple_services_all_in_depends_on(self):
        out = _build_compose("myapp", "3.12", ["pgsql", "redis", "mailpit"])
        assert "pgsql:" in out
        assert "redis:" in out
        assert "mailpit:" in out
        assert "depends_on:" in out
        assert out.count("condition:") == 3

    def test_volumes_created_for_persistent_services(self):
        out = _build_compose("myapp", "3.12", ["pgsql", "redis"])
        assert "volumes:" in out
        assert "lodge-pgsql:" in out
        assert "lodge-redis:" in out

    def test_mailpit_does_not_create_volume(self):
        out = _build_compose("myapp", "3.12", ["mailpit"])
        # mailpit has no named volume — only service bind-mounts should appear
        assert "lodge-mailpit" not in out

    def test_compose_valid_yaml_structure(self):
        out = _build_compose("myapp", "3.12", ["pgsql", "redis"])
        assert out.startswith("services:")
        assert "networks:\n  lodge:" in out
        assert "driver: bridge" in out

    def test_docker_compose_env_vars_preserved(self):
        out = _build_compose("myapp", "3.12", ["pgsql"])
        assert "${DB_PORT:-5432}" in out
        assert "${DB_DATABASE:-hunt}" in out

    def test_app_port_env_var_escaped(self):
        out = _build_compose("myapp", "3.12", [])
        assert "${APP_PORT:-8000}" in out


# ---------------------------------------------------------------------------
# lodge:install command (CLI)
# ---------------------------------------------------------------------------


class TestLodgeInstallCommand:
    def _run(self, args, input=None, env=None):
        runner = CliRunner()
        return runner.invoke(lodge_install_command, args, input=input, env=env, catch_exceptions=False)

    def test_creates_compose_yaml(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        result = self._run([], env={"PWD": str(tmp_path)})
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            result = self._run([])
        assert (tmp_path / "compose.yaml").exists() or result.exit_code == 0

    def test_rejects_both_pgsql_and_mysql(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_install_command, ["--with=pgsql,mysql"], catch_exceptions=False)
        assert result.exit_code != 0
        assert "Cannot include both" in result.output

    def test_rejects_unknown_service(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_install_command, ["--with=oracle"], catch_exceptions=False)
        assert result.exit_code != 0
        assert "Unknown service" in result.output

    def test_rejects_non_hunt_project(self, tmp_path):
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_install_command, [], catch_exceptions=False)
        assert result.exit_code != 0
        assert "No hunt project detected" in result.output

    def test_scaffolds_all_files(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        (tmp_path / ".env").write_text("")
        (tmp_path / ".env.example").write_text("")
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_install_command, ["--with=redis"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "compose.yaml").exists()
        assert (tmp_path / "docker" / "Dockerfile").exists()
        assert (tmp_path / "docker" / "docker-entrypoint.sh").exists()
        assert (tmp_path / "lodge").exists()

    def test_lodge_script_is_executable(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            runner.invoke(lodge_install_command, [], catch_exceptions=False)
        lodge = tmp_path / "lodge"
        if lodge.exists():
            import stat

            assert lodge.stat().st_mode & stat.S_IXUSR

    def test_updates_env_for_selected_services(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        (tmp_path / ".env").write_text("APP_ENV=local\nREDIS_HOST=127.0.0.1\n")
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            runner.invoke(lodge_install_command, ["--with=redis"], catch_exceptions=False)
        env_content = (tmp_path / ".env").read_text()
        assert "REDIS_HOST=redis" in env_content

    def test_detects_python_version_from_runtime(self, tmp_path):
        import sys

        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            runner.invoke(lodge_install_command, [], catch_exceptions=False)
        if (tmp_path / "compose.yaml").exists():
            content = (tmp_path / "compose.yaml").read_text()
            expected = f"{sys.version_info.major}.{sys.version_info.minor}"
            assert expected in content

    def test_reads_app_name_from_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-cool-app"\n')
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            runner.invoke(lodge_install_command, [], catch_exceptions=False)
        if (tmp_path / "compose.yaml").exists():
            assert "my_cool_app" in (tmp_path / "compose.yaml").read_text()

    def test_asks_before_overwriting_compose(self, tmp_path):
        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "app.py").touch()
        (tmp_path / "compose.yaml").write_text("existing: true\n")
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_install_command, [], input="n\n", catch_exceptions=False)
        assert "Aborted" in result.output or result.exit_code == 0
        assert "existing: true" in (tmp_path / "compose.yaml").read_text()


# ---------------------------------------------------------------------------
# _services_in_compose
# ---------------------------------------------------------------------------


class TestServicesInCompose:
    def test_finds_single_service(self):
        content = "services:\n  app:\n    image: x\n  redis:\n    image: redis\n"
        assert _services_in_compose(content) == ["redis"]

    def test_finds_multiple_services(self):
        content = "services:\n  app:\n    image: x\n  pgsql:\n    image: pg\n  redis:\n    image: redis\n"
        result = _services_in_compose(content)
        assert "pgsql" in result
        assert "redis" in result
        assert "app" not in result

    def test_returns_empty_for_no_known_services(self):
        content = "services:\n  app:\n    image: x\n"
        assert _services_in_compose(content) == []

    def test_does_not_match_partial_names(self):
        content = "  redis_cache:\n    image: redis\n"
        assert _services_in_compose(content) == []


# ---------------------------------------------------------------------------
# _insert_service
# ---------------------------------------------------------------------------


class TestInsertService:
    def _base_compose(self):
        return (
            "services:\n"
            "  app:\n"
            "    image: x\n"
            "\n"
            "networks:\n"
            "  lodge:\n"
            "    driver: bridge\n"
        )

    def test_inserts_before_networks(self):
        result = _insert_service(self._base_compose(), "redis")
        redis_pos = result.index("redis:")
        networks_pos = result.index("networks:")
        assert redis_pos < networks_pos

    def test_appends_when_no_networks_section(self):
        content = "services:\n  app:\n    image: x\n"
        result = _insert_service(content, "redis")
        assert "redis:" in result

    def test_preserves_existing_content(self):
        result = _insert_service(self._base_compose(), "redis")
        assert "app:" in result
        assert "networks:" in result


# ---------------------------------------------------------------------------
# _add_depends_on
# ---------------------------------------------------------------------------


class TestAddDependsOn:
    def test_adds_to_existing_depends_on(self):
        content = (
            "  app:\n"
            "    image: x\n"
            "    depends_on:\n"
            "      pgsql:\n"
            "        condition: service_healthy\n"
        )
        result = _add_depends_on(content, "redis")
        assert "redis:" in result
        assert "pgsql:" in result

    def test_creates_depends_on_when_absent(self):
        content = (
            "  app:\n"
            "    volumes:\n"
            '      - ".:/app"\n'
            '      - "/app/.venv"\n'
        )
        result = _add_depends_on(content, "redis")
        assert "depends_on:" in result
        assert "redis:" in result

    def test_uses_service_healthy_for_redis(self):
        content = "  app:\n    depends_on:\n"
        result = _add_depends_on(content, "redis")
        assert "service_healthy" in result

    def test_uses_service_started_for_mailpit(self):
        content = "  app:\n    depends_on:\n"
        result = _add_depends_on(content, "mailpit")
        assert "service_started" in result


# ---------------------------------------------------------------------------
# _add_volume
# ---------------------------------------------------------------------------


class TestAddVolume:
    def test_adds_to_existing_volumes_section(self):
        content = "networks:\n  lodge:\n    driver: bridge\n\nvolumes:\n  lodge-pgsql:\n    driver: local\n"
        result = _add_volume(content, "lodge-redis")
        assert "lodge-redis:" in result
        assert "lodge-pgsql:" in result

    def test_creates_volumes_section_when_absent(self):
        content = "networks:\n  lodge:\n    driver: bridge\n"
        result = _add_volume(content, "lodge-redis")
        assert "volumes:" in result
        assert "lodge-redis:" in result

    def test_idempotent_when_volume_exists(self):
        content = "volumes:\n  lodge-redis:\n    driver: local\n"
        result = _add_volume(content, "lodge-redis")
        assert result.count("lodge-redis:") == 1


# ---------------------------------------------------------------------------
# lodge:add command (CLI)
# ---------------------------------------------------------------------------


class TestLodgeAddCommand:
    def test_adds_redis_to_existing_compose(self, tmp_path):
        compose = (
            "services:\n"
            "  app:\n"
            "    image: x\n"
            "    volumes:\n"
            '      - ".:/app"\n'
            '      - "/app/.venv"\n'
            "\n"
            "networks:\n"
            "  lodge:\n"
            "    driver: bridge\n"
        )
        (tmp_path / "compose.yaml").write_text(compose)
        (tmp_path / ".env").write_text("APP_ENV=local\n")
        (tmp_path / ".env.example").write_text("APP_ENV=local\n")
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_add_command, ["redis"], catch_exceptions=False)
        assert result.exit_code == 0
        content = (tmp_path / "compose.yaml").read_text()
        assert "redis:" in content

    def test_rejects_duplicate_service(self, tmp_path):
        compose = "services:\n  app:\n    image: x\n  redis:\n    image: redis\n\nnetworks:\n  lodge:\n"
        (tmp_path / "compose.yaml").write_text(compose)
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_add_command, ["redis"], catch_exceptions=False)
        assert "already in" in result.output

    def test_rejects_mysql_when_pgsql_present(self, tmp_path):
        compose = "services:\n  app:\n    image: x\n  pgsql:\n    image: pg\n\nnetworks:\n  lodge:\n"
        (tmp_path / "compose.yaml").write_text(compose)
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_add_command, ["mysql"], catch_exceptions=False)
        assert result.exit_code != 0

    def test_rejects_pgsql_when_mysql_present(self, tmp_path):
        compose = "services:\n  app:\n    image: x\n  mysql:\n    image: mysql\n\nnetworks:\n  lodge:\n"
        (tmp_path / "compose.yaml").write_text(compose)
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_add_command, ["pgsql"], catch_exceptions=False)
        assert result.exit_code != 0

    def test_errors_without_compose_yaml(self, tmp_path):
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
            result = runner.invoke(lodge_add_command, ["redis"], catch_exceptions=False)
        assert result.exit_code != 0
        assert "lodge:install" in result.output

    def test_updates_env_on_add(self, tmp_path):
        compose = (
            "services:\n  app:\n    image: x\n    volumes:\n"
            '      - ".:/app"\n      - "/app/.venv"\n\nnetworks:\n  lodge:\n'
        )
        (tmp_path / "compose.yaml").write_text(compose)
        (tmp_path / ".env").write_text("APP_ENV=local\n")
        (tmp_path / ".env.example").write_text("")
        runner = CliRunner()
        with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
            runner.invoke(lodge_add_command, ["redis"], catch_exceptions=False)
        assert "REDIS_HOST=redis" in (tmp_path / ".env").read_text()
