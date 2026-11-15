"""Regression tests for GEN-09 (CLI side): hunt down / hunt up."""

import json

import pytest
from click.testing import CliRunner

from hunt.console.commands import maintenance
from hunt.console.commands.maintenance import down_command, up_command


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sentinel = tmp_path / ".maintenance"
    monkeypatch.setattr(maintenance, "_sentinel_path", lambda: sentinel)
    return tmp_path


def _bootstrap(project, source):
    (project / "bootstrap").mkdir()
    (project / "bootstrap" / "app.py").write_text(source)


def test_down_writes_json_object_and_up_removes_it(project):
    result = CliRunner().invoke(down_command, ["--message", "<b>brb</b>", "--retry", "120"])

    assert result.exit_code == 0
    data = json.loads((project / ".maintenance").read_text())
    assert data == {"message": "<b>brb</b>", "retry_after": 120}
    assert [p.name for p in project.iterdir()] == [".maintenance"]

    result = CliRunner().invoke(up_command, [])
    assert "now live" in result.output
    assert not (project / ".maintenance").exists()


def test_down_warns_when_middleware_is_not_registered(project):
    _bootstrap(project, "global_middleware = [\n    StartSession,\n    # MaintenanceMode,\n]\n")

    result = CliRunner().invoke(down_command, [])

    assert result.exit_code == 0
    assert "Warning: MaintenanceMode does not appear to be registered" in result.output


def test_down_warns_outside_an_application(project):
    result = CliRunner().invoke(down_command, [])

    assert "Warning: MaintenanceMode" in result.output


def test_down_does_not_warn_when_middleware_is_registered(project):
    _bootstrap(
        project,
        "from hunt.http.middleware.maintenance import MaintenanceMode\n"
        "global_middleware = [\n    MaintenanceMode,\n    StartSession,\n]\n",
    )

    result = CliRunner().invoke(down_command, [])

    assert "Warning" not in result.output


def test_negative_retry_is_rejected(project):
    result = CliRunner().invoke(down_command, ["--retry", "-5"])

    assert result.exit_code != 0
    assert not (project / ".maintenance").exists()


def test_sentinel_path_comes_from_the_middleware_module(tmp_path, monkeypatch):
    """The CLI must write the file the middleware reads."""
    import hunt.http.middleware.maintenance as middleware_module

    monkeypatch.chdir(tmp_path)
    expected = tmp_path / "custom" / ".maintenance"
    monkeypatch.setattr(middleware_module, "maintenance_file", lambda: expected, raising=False)

    result = CliRunner().invoke(down_command, [])

    assert result.exit_code == 0
    assert json.loads(expected.read_text())["retry_after"] == 60

    CliRunner().invoke(up_command, [])
    assert not expected.exists()


def test_up_when_already_live(project):
    result = CliRunner().invoke(up_command, [])

    assert result.exit_code == 0
    assert "already live" in result.output
