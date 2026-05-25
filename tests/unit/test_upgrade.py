"""Tests for the upgrade command."""

import json

import pytest
from click.testing import CliRunner

from hunt.console.commands.new import _file_hash
from hunt.console.commands.upgrade import upgrade_command

_BOOTSTRAP = """\
from routes.web import register as web_routes
from hunt.http.router import Router


def create_app():
    router = Router()
    web_routes(router)
    return router

application = create_app()
"""

_SCAFFOLD_SAMPLE = {
    "app/models/user.py": "# user model\n",
    "routes/auth.py": "# auth routes\n",
}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    (tmp_path / "bootstrap").mkdir()
    (tmp_path / "bootstrap" / "app.py").write_text(_BOOTSTRAP)
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Guard: bootstrap/app.py must exist
# ---------------------------------------------------------------------------


def test_error_when_no_bootstrap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(upgrade_command, [])
    assert result.exit_code != 0
    assert "bootstrap/app.py not found" in result.output


# ---------------------------------------------------------------------------
# Adding missing files
# ---------------------------------------------------------------------------


def test_adds_missing_scaffold_files(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", _SCAFFOLD_SAMPLE)

    result = CliRunner().invoke(upgrade_command, [])

    assert result.exit_code == 0
    for rel in _SCAFFOLD_SAMPLE:
        assert (project / rel).exists()


def test_reports_added_files(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", _SCAFFOLD_SAMPLE)

    result = CliRunner().invoke(upgrade_command, [])

    assert "file(s) added" in result.output
    for rel in _SCAFFOLD_SAMPLE:
        assert f"+ {rel}" in result.output


# ---------------------------------------------------------------------------
# Skipping already up-to-date files
# ---------------------------------------------------------------------------


def test_skips_files_matching_canonical_content(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", _SCAFFOLD_SAMPLE)
    for rel, content in _SCAFFOLD_SAMPLE.items():
        dest = project / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

    # Pre-apply bootstrap patches so they don't fire on the run under test
    CliRunner().invoke(upgrade_command, [])
    result = CliRunner().invoke(upgrade_command, [])

    assert "Already up to date" in result.output


# ---------------------------------------------------------------------------
# Skipping customised files
# ---------------------------------------------------------------------------


def test_skips_customised_files(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", _SCAFFOLD_SAMPLE)
    # Write files with different content and no lock file (simulates user edits
    # before lock file existed, or edits after scaffold)
    for rel in _SCAFFOLD_SAMPLE:
        dest = project / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("# customised by user\n")

    result = CliRunner().invoke(upgrade_command, [])

    assert "customised" in result.output
    for rel in _SCAFFOLD_SAMPLE:
        assert (project / rel).read_text() == "# customised by user\n"


# ---------------------------------------------------------------------------
# Upgrading unmodified files
# ---------------------------------------------------------------------------


def test_upgrades_unmodified_files(project, monkeypatch):
    old_content = "# old scaffold content\n"
    new_content = "# new scaffold content\n"
    scaffold = {"app/models/user.py": new_content}

    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", scaffold)

    # Write the old content and record its hash in the lock file
    dest = project / "app" / "models" / "user.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(old_content)

    lock = {"version": 1, "files": {"app/models/user.py": _file_hash(old_content)}}
    (project / ".hunt").mkdir()
    (project / ".hunt" / "scaffold.lock").write_text(json.dumps(lock))

    result = CliRunner().invoke(upgrade_command, [])

    assert result.exit_code == 0
    assert dest.read_text() == new_content
    assert "↑ app/models/user.py" in result.output


def test_does_not_upgrade_customised_file_even_with_lock(project, monkeypatch):
    old_content = "# old scaffold content\n"
    user_content = "# my custom edits\n"
    new_content = "# new scaffold content\n"
    scaffold = {"app/models/user.py": new_content}

    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", scaffold)

    dest = project / "app" / "models" / "user.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(user_content)

    # Lock records old_content hash — user has since edited the file
    lock = {"version": 1, "files": {"app/models/user.py": _file_hash(old_content)}}
    (project / ".hunt").mkdir()
    (project / ".hunt" / "scaffold.lock").write_text(json.dumps(lock))

    CliRunner().invoke(upgrade_command, [])

    assert dest.read_text() == user_content


# ---------------------------------------------------------------------------
# Lock file written
# ---------------------------------------------------------------------------


def test_writes_lock_file_after_upgrade(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", _SCAFFOLD_SAMPLE)

    CliRunner().invoke(upgrade_command, [])

    lock_file = project / ".hunt" / "scaffold.lock"
    assert lock_file.exists()
    data = json.loads(lock_file.read_text())
    assert data["version"] == 1
    for rel in _SCAFFOLD_SAMPLE:
        assert rel in data["files"]


def test_lock_file_stores_canonical_hash(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", _SCAFFOLD_SAMPLE)

    CliRunner().invoke(upgrade_command, [])

    data = json.loads((project / ".hunt" / "scaffold.lock").read_text())
    for rel, content in _SCAFFOLD_SAMPLE.items():
        assert data["files"][rel] == _file_hash(content)


# ---------------------------------------------------------------------------
# Bootstrap patching
# ---------------------------------------------------------------------------


def test_patches_bootstrap_with_auth_routes(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", {})

    CliRunner().invoke(upgrade_command, [])

    bootstrap = (project / "bootstrap" / "app.py").read_text()
    assert "from routes.auth import register as auth_routes" in bootstrap
    assert "auth_routes(router)" in bootstrap


def test_patches_bootstrap_with_admin_routes(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", {})

    CliRunner().invoke(upgrade_command, [])

    bootstrap = (project / "bootstrap" / "app.py").read_text()
    assert "from routes.admin import register as admin_routes" in bootstrap
    assert "admin_routes(router)" in bootstrap


def test_does_not_duplicate_patches(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", {})

    CliRunner().invoke(upgrade_command, [])
    CliRunner().invoke(upgrade_command, [])

    bootstrap = (project / "bootstrap" / "app.py").read_text()
    assert bootstrap.count("auth_routes(router)") == 1
    assert bootstrap.count("admin_routes(router)") == 1
