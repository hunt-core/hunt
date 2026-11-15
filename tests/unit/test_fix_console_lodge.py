"""Regression tests for CLI-03 (lodge:install / lodge:add)."""

from __future__ import annotations

import re
import stat
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hunt.console.commands import lodge_install
from hunt.console.commands.lodge_add import lodge_add_command
from hunt.console.commands.lodge_install import (
    _SERVICES,
    _apply_service_env,
    _build_compose,
    _get_env,
    _set_env,
    lodge_install_command,
)


def _project(tmp_path, env: str | None = None):
    (tmp_path / "bootstrap").mkdir()
    (tmp_path / "bootstrap" / "app.py").touch()
    if env is not None:
        (tmp_path / ".env").write_text(env)
    return tmp_path


def _install(tmp_path, args, input=None):
    with patch("hunt.console.commands.lodge_install.Path.cwd", return_value=tmp_path):
        return CliRunner().invoke(lodge_install_command, args, input=input, catch_exceptions=False)


def _add(tmp_path, service):
    with patch("hunt.console.commands.lodge_add.Path.cwd", return_value=tmp_path):
        return CliRunner().invoke(lodge_add_command, [service], catch_exceptions=False)


def _port_mappings(compose: str) -> list[str]:
    """Every list item under a `ports:` key."""
    mappings: list[str] = []
    in_ports = False
    for line in compose.splitlines():
        stripped = line.strip()
        if stripped == "ports:":
            in_ports = True
            continue
        if in_ports and stripped.startswith("- "):
            mappings.append(stripped[2:].strip('"'))
            continue
        in_ports = False
    return mappings


# ---------------------------------------------------------------------------
# Compose content
# ---------------------------------------------------------------------------


class TestComposeHardening:
    def test_every_port_binds_to_loopback(self):
        compose = _build_compose("myapp", "3.12", ["pgsql", "redis", "mailpit", "minio"])
        mappings = _port_mappings(compose)
        assert len(mappings) == 7  # app + pgsql + redis + 2 mailpit + 2 minio
        assert all(m.startswith("127.0.0.1:") for m in mappings), mappings

    def test_mysql_port_binds_to_loopback_and_root_is_local_only(self):
        compose = _build_compose("myapp", "3.12", ["mysql"])
        assert all(m.startswith("127.0.0.1:") for m in _port_mappings(compose))
        assert 'MYSQL_ROOT_HOST: "%"' not in compose

    @pytest.mark.parametrize("services", [["pgsql", "redis", "mailpit", "minio"], ["mysql"]])
    def test_host_ports_use_forward_variables(self, services):
        compose = _build_compose("myapp", "3.12", services)
        service_mappings = [m for m in _port_mappings(compose) if "APP_PORT" not in m]
        assert service_mappings
        for mapping in service_mappings:
            assert re.match(r"^127\.0\.0\.1:\$\{FORWARD_[A-Z_]+_PORT:-\d+\}:\d+$", mapping), mapping
        # In-container port variables must not double as the published port.
        for name in ("DB_PORT", "REDIS_PORT", "MAIL_PORT"):
            assert "${" + name + ":" not in compose

    def test_images_are_pinned(self):
        compose = _build_compose("myapp", "3.12", ["pgsql", "redis", "mailpit", "minio"])
        compose += _build_compose("myapp", "3.12", ["mysql"])
        images = re.findall(r'^\s+image: "([^"]+)"$', compose, re.MULTILINE)
        images = [i for i in images if not i.endswith("/app")]
        assert len(images) == 5
        for image in images:
            # Unwrap "${VAR:-default}" to the default.
            m = re.match(r"^\$\{[A-Z_]+:-(.+)\}$", image)
            ref = m.group(1) if m else image
            if "@sha256:" in ref:
                # Pinned by digest, which is stricter than any tag.
                assert re.fullmatch(r"[0-9a-f]{64}", ref.split("@sha256:", 1)[1]), image
                continue
            tag = ref.rsplit(":", 1)[1]
            assert tag not in ("latest", "alpine", "stable"), image
            assert re.search(r"\d+\.\d+", tag) or tag.startswith("RELEASE."), image
        assert "postgres:16.16" in compose
        assert "mysql:8.0.46" in compose
        assert "axllent/mailpit:v1.33.1" in compose


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------


class TestEnvHandling:
    def test_minio_writes_the_key_the_generated_config_reads(self):
        from hunt.console.commands import new

        assert '"AWS_ENDPOINT_URL"' in new._CONFIG_FILESYSTEMS
        content, _ = _apply_service_env("", "minio", generate_secrets=False)
        assert _get_env(content, "AWS_ENDPOINT_URL") == "http://minio:9000"
        assert _get_env(content, "AWS_ENDPOINT") is None

    def test_written_keys_are_read_by_generated_config(self):
        from hunt.console.commands import new

        templates = "\n".join(v for k, v in vars(new).items() if k.startswith("_CONFIG_") and isinstance(v, str))
        for svc in _SERVICES:
            for key in lodge_install._ENV_UPDATES[svc]:
                assert f'"{key}"' in templates, f"{svc} writes {key}, which no generated config file reads"

    def test_existing_credentials_are_not_overwritten(self, tmp_path):
        _project(
            tmp_path,
            "DB_CONNECTION=sqlite\nDB_DATABASE=database/database.sqlite\nDB_USERNAME=alice\nDB_PASSWORD=hunter2\n",
        )
        result = _install(tmp_path, ["--with=pgsql"])
        assert result.exit_code == 0
        env = (tmp_path / ".env").read_text()
        assert _get_env(env, "DB_USERNAME") == "alice"
        assert _get_env(env, "DB_PASSWORD") == "hunter2"
        # Wiring keys are still pointed at the container.
        assert _get_env(env, "DB_CONNECTION") == "pgsql"
        assert _get_env(env, "DB_HOST") == "pgsql"
        assert _get_env(env, "DB_PORT") == "5432"
        # A SQLite file path is not a usable server database name.
        assert _get_env(env, "DB_DATABASE") == "hunt"
        assert "DB_PASSWORD" in result.output  # reported as kept

    def test_existing_aws_keys_are_not_overwritten(self, tmp_path):
        _project(tmp_path, "AWS_ACCESS_KEY_ID=AKIAREAL\nAWS_SECRET_ACCESS_KEY=realsecret\nAWS_BUCKET=prod-bucket\n")
        assert _install(tmp_path, ["--with=minio"]).exit_code == 0
        env = (tmp_path / ".env").read_text()
        assert _get_env(env, "AWS_ACCESS_KEY_ID") == "AKIAREAL"
        assert _get_env(env, "AWS_SECRET_ACCESS_KEY") == "realsecret"
        assert _get_env(env, "AWS_BUCKET") == "prod-bucket"

    def test_existing_database_name_is_kept(self):
        content, kept = _apply_service_env("DB_DATABASE=shop\n", "mysql", generate_secrets=False)
        assert _get_env(content, "DB_DATABASE") == "shop"
        assert "DB_DATABASE" in kept

    def test_empty_credentials_get_a_generated_secret_in_env_only(self, tmp_path):
        _project(tmp_path, "DB_PASSWORD=\n")
        (tmp_path / ".env.example").write_text("DB_PASSWORD=\n")
        assert _install(tmp_path, ["--with=pgsql"]).exit_code == 0
        password = _get_env((tmp_path / ".env").read_text(), "DB_PASSWORD")
        assert password and password != "secret" and len(password) >= 16
        # The committed example never receives the real value.
        assert _get_env((tmp_path / ".env.example").read_text(), "DB_PASSWORD") == "secret"

    def test_set_env_keeps_backslashes_literal(self):
        assert "K=a\\1b" in _set_env("K=old\n", "K", "a\\1b")

    def test_lodge_add_keeps_existing_credentials(self, tmp_path):
        assert _install(_project(tmp_path, "APP_ENV=local\nDB_PASSWORD=hunter2\n"), []).exit_code == 0
        result = _add(tmp_path, "pgsql")
        assert result.exit_code == 0
        env = (tmp_path / ".env").read_text()
        assert _get_env(env, "DB_PASSWORD") == "hunter2"
        assert _get_env(env, "DB_HOST") == "pgsql"
        compose = (tmp_path / "compose.yaml").read_text()
        assert all(m.startswith("127.0.0.1:") for m in _port_mappings(compose))

    def test_lodge_add_minio_uses_endpoint_url(self, tmp_path):
        assert _install(_project(tmp_path, "APP_ENV=local\n"), []).exit_code == 0
        assert _add(tmp_path, "minio").exit_code == 0
        env = (tmp_path / ".env").read_text()
        assert _get_env(env, "AWS_ENDPOINT_URL") == "http://minio:9000"


# ---------------------------------------------------------------------------
# Overwrite protection
# ---------------------------------------------------------------------------

_GENERATED = ("compose.yaml", "docker/Dockerfile", "docker/docker-entrypoint.sh", "lodge")


class TestOverwriteProtection:
    def _seed(self, tmp_path):
        _project(tmp_path, "REDIS_HOST=127.0.0.1\n")
        (tmp_path / "docker").mkdir()
        for rel in _GENERATED:
            (tmp_path / rel).write_text(f"# custom {rel}\n")

    def test_existing_files_preserved_without_force_non_interactive(self, tmp_path):
        self._seed(tmp_path)
        result = _install(tmp_path, ["--with=redis"])  # no stdin: every prompt defaults to "no"
        assert result.exit_code == 0
        for rel in _GENERATED:
            assert (tmp_path / rel).read_text() == f"# custom {rel}\n", rel
        # compose.yaml was kept, so .env must not be pointed at services it does not define.
        assert (tmp_path / ".env").read_text() == "REDIS_HOST=127.0.0.1\n"

    def test_each_file_is_confirmed_separately(self, tmp_path):
        self._seed(tmp_path)
        # compose: yes, Dockerfile: no, entrypoint: no, lodge: yes
        result = _install(tmp_path, ["--with=redis"], input="y\nn\nn\ny\n")
        assert result.exit_code == 0
        assert (tmp_path / "compose.yaml").read_text().startswith("services:")
        assert (tmp_path / "docker" / "Dockerfile").read_text() == "# custom docker/Dockerfile\n"
        assert (tmp_path / "docker" / "docker-entrypoint.sh").read_text() == "# custom docker/docker-entrypoint.sh\n"
        assert (tmp_path / "lodge").read_text().startswith("#!/usr/bin/env bash")
        assert "REDIS_HOST=redis" in (tmp_path / ".env").read_text()

    def test_force_overwrites_everything(self, tmp_path):
        self._seed(tmp_path)
        result = _install(tmp_path, ["--with=redis", "--force"])
        assert result.exit_code == 0
        for rel in _GENERATED:
            assert "# custom" not in (tmp_path / rel).read_text(), rel
        assert (tmp_path / "lodge").stat().st_mode & stat.S_IXUSR

    def test_rerun_with_identical_files_does_not_prompt(self, tmp_path):
        _project(tmp_path)
        assert _install(tmp_path, []).exit_code == 0
        result = _install(tmp_path, [])
        assert result.exit_code == 0
        assert "Overwrite?" not in result.output


# ---------------------------------------------------------------------------
# Input validation and hints
# ---------------------------------------------------------------------------


class TestValidationAndHints:
    @pytest.mark.parametrize("version", ['3.12"\n    privileged: "true', "latest", "3", "3.12.1", ""])
    def test_rejects_bad_python_version(self, tmp_path, version):
        _project(tmp_path)
        result = _install(tmp_path, [f"--python={version}"])
        assert result.exit_code != 0
        assert not (tmp_path / "compose.yaml").exists()
        assert not (tmp_path / "docker").exists()

    @pytest.mark.parametrize("name", ["evil$(id)", "a b", "x/../y", "-lead", "näme"])
    def test_rejects_bad_app_name(self, tmp_path, name):
        _project(tmp_path)
        (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
        result = _install(tmp_path, [])
        assert result.exit_code != 0
        assert "Invalid application name" in result.output
        assert not (tmp_path / "compose.yaml").exists()

    def test_app_name_is_lowercased_for_the_image(self, tmp_path):
        _project(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "My-App"\n')
        assert _install(tmp_path, []).exit_code == 0
        assert '"my_app/app"' in (tmp_path / "compose.yaml").read_text()

    @pytest.mark.parametrize(
        ("service", "extra", "package"),
        [("pgsql", "postgres", "psycopg2-binary"), ("redis", "redis", "redis"), ("minio", "s3", "boto3")],
    )
    def test_install_prints_driver_hint(self, tmp_path, service, extra, package):
        _project(tmp_path)
        result = _install(tmp_path, [f"--with={service}"])
        assert result.exit_code == 0
        assert f'pip install "hunt-framework[{extra}]"' in result.output
        assert f"pip install {package}" in result.output

    def test_add_prints_driver_hint(self, tmp_path):
        assert _install(_project(tmp_path), []).exit_code == 0
        result = _add(tmp_path, "redis")
        assert 'pip install "hunt-framework[redis]"' in result.output

    def test_no_hint_for_services_without_extra_driver(self, tmp_path):
        _project(tmp_path)
        result = _install(tmp_path, ["--with=mysql,mailpit"])
        assert result.exit_code == 0
        assert "pip install" not in result.output
