"""Tests for the key:generate command."""

import pytest
from click.testing import CliRunner

from hunt.console.commands.key_generate import key_generate_command


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_show_flag_prints_key_without_writing(project):
    result = CliRunner().invoke(key_generate_command, ["--show"])

    assert result.exit_code == 0
    assert result.output.strip().startswith("base64:")
    assert not (project / ".env").exists()


def test_show_flag_key_is_base64_encoded(project):
    result = CliRunner().invoke(key_generate_command, ["--show"])

    key = result.output.strip()
    import base64

    raw = key.removeprefix("base64:")
    decoded = base64.urlsafe_b64decode(raw + "==")
    assert len(decoded) == 32


def test_writes_key_when_env_has_app_key_line(project):
    (project / ".env").write_text("APP_NAME=MyApp\nAPP_KEY=\nDEBUG=false\n")

    result = CliRunner().invoke(key_generate_command, [])

    assert result.exit_code == 0
    content = (project / ".env").read_text()
    assert "APP_KEY=base64:" in content
    assert "APP_KEY=\n" not in content


def test_appends_key_when_env_has_no_app_key_line(project):
    (project / ".env").write_text("APP_NAME=MyApp\n")

    CliRunner().invoke(key_generate_command, [])

    content = (project / ".env").read_text()
    assert "APP_KEY=base64:" in content


def test_key_is_different_each_run(project):
    (project / ".env").write_text("APP_KEY=\n")

    CliRunner().invoke(key_generate_command, [])
    key1 = (project / ".env").read_text()

    (project / ".env").write_text("APP_KEY=\n")
    CliRunner().invoke(key_generate_command, [])
    key2 = (project / ".env").read_text()

    assert key1 != key2


def test_error_when_no_env_file(project):
    result = CliRunner().invoke(key_generate_command, [])

    assert result.exit_code == 0
    assert "No .env file found" in result.output
    assert not (project / ".env").exists()
