"""Regression tests for CLI-L1 and GEN-02 (hunt upgrade)."""

import ast
import tomllib

import pytest
from click.testing import CliRunner

from hunt.console.commands import upgrade as upgrade_module
from hunt.console.commands.new import framework_requirement
from hunt.console.commands.upgrade import _plan_pyproject, _rewrite_requirement, upgrade_command

_BOOTSTRAP = """\
from routes.web import register as web_routes
from hunt.http.router import Router


def create_app():
    router = Router()
    web_routes(router)
    return router

application = create_app()
"""

_PYPROJECT = """\
[project]
name = "hunt"
version = "0.1.0"
dependencies = [
    "hunt",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", 'hunt>=0.1']
"""


@pytest.fixture()
def project(tmp_path, monkeypatch):
    (tmp_path / "bootstrap").mkdir()
    (tmp_path / "bootstrap" / "app.py").write_text(_BOOTSTRAP)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", {})
    monkeypatch.setattr("hunt.console.commands.upgrade._NEW_CONFIG_FILES", {})
    return tmp_path


def _bootstrap(project):
    return (project / "bootstrap" / "app.py").read_text()


# -- CLI-L1 -----------------------------------------------------------------


def test_non_interactive_run_without_yes_leaves_bootstrap_untouched(project):
    result = CliRunner().invoke(upgrade_command, [])

    assert result.exit_code == 0
    assert _bootstrap(project) == _BOOTSTRAP
    assert "routes/admin.py registration" in result.output
    assert "--yes" in result.output


def test_dry_run_writes_nothing(project, monkeypatch):
    monkeypatch.setattr("hunt.console.commands.upgrade._SCAFFOLD_FILES", {"routes/auth.py": "# auth\n"})
    (project / "pyproject.toml").write_text(_PYPROJECT)

    result = CliRunner().invoke(upgrade_command, ["--dry-run", "--yes", "--with-auth"])

    assert result.exit_code == 0
    assert "[dry-run]" in result.output
    assert _bootstrap(project) == _BOOTSTRAP
    assert (project / "pyproject.toml").read_text() == _PYPROJECT
    assert not (project / "routes").exists()
    assert not (project / ".hunt").exists()


def test_yes_does_not_enable_auth_routes_without_opt_in(project):
    result = CliRunner().invoke(upgrade_command, ["--yes"])

    content = _bootstrap(project)
    assert "routes.auth" not in content
    assert "auth_routes" not in content
    assert "admin_routes(router)" in content
    assert "--with-auth" in result.output


def test_interactive_confirmation_applies_patches(project, monkeypatch):
    monkeypatch.setattr(upgrade_module, "_is_interactive", lambda: True)

    # First answer: register auth routes? no. Second: apply patches? yes.
    result = CliRunner().invoke(upgrade_command, [], input="n\ny\n")

    assert result.exit_code == 0
    content = _bootstrap(project)
    assert "auth_routes" not in content
    assert "admin_routes(router)" in content


def test_interactive_decline_leaves_bootstrap_untouched(project, monkeypatch):
    monkeypatch.setattr(upgrade_module, "_is_interactive", lambda: True)

    CliRunner().invoke(upgrade_command, [], input="y\nn\n")

    assert _bootstrap(project) == _BOOTSTRAP


def test_patched_bootstrap_is_valid_python_with_matching_indentation(project):
    CliRunner().invoke(upgrade_command, ["--yes", "--with-auth"])

    content = _bootstrap(project)
    ast.parse(content)
    assert "    auth_routes(router)\n" in content
    assert "    admin_routes(router)\n" in content
    assert content.index("import register as auth_routes") < content.index("auth_routes(router)")


def test_existing_alias_gets_call_without_second_import(project):
    aliased = _BOOTSTRAP.replace(
        "from routes.web import register as web_routes\n",
        "from routes.web import register as web_routes\nfrom routes.auth import register as register_auth\n",
    )
    (project / "bootstrap" / "app.py").write_text(aliased)

    CliRunner().invoke(upgrade_command, ["--yes"])

    content = _bootstrap(project)
    ast.parse(content)
    assert content.count("from routes.auth") == 1
    assert "    register_auth(router)\n" in content
    assert "auth_routes(router)" not in content


def test_aliased_and_called_import_is_left_alone(project):
    wired = _BOOTSTRAP.replace(
        "from routes.web import register as web_routes\n",
        "from routes.web import register as web_routes\n"
        "from routes.auth import register as register_auth\n"
        "from routes.admin import register as register_admin\n",
    ).replace(
        "    web_routes(router)\n", "    web_routes(router)\n    register_auth(router)\n    register_admin(router)\n"
    )
    (project / "bootstrap" / "app.py").write_text(wired)

    result = CliRunner().invoke(upgrade_command, ["--yes", "--with-auth"])

    assert _bootstrap(project) == wired
    assert "Already up to date" in result.output


def test_call_without_import_gets_its_import(project):
    """State left behind by the old upgrade: the call exists, the import does not."""
    broken = _BOOTSTRAP.replace("    web_routes(router)\n", "    web_routes(router)\n    auth_routes(router)\n")
    (project / "bootstrap" / "app.py").write_text(broken)

    CliRunner().invoke(upgrade_command, ["--yes"])

    content = _bootstrap(project)
    ast.parse(content)
    assert content.count("from routes.auth import register as auth_routes") == 1
    assert content.count("auth_routes(router)") == 1
    assert content.index("import register as auth_routes") < content.index("auth_routes(router)")


def test_no_insertion_point_adds_neither_import_nor_call(project):
    odd = "from hunt.http.router import Router\n\nrouter = Router()\n"
    (project / "bootstrap" / "app.py").write_text(odd)

    result = CliRunner().invoke(upgrade_command, ["--yes", "--with-auth"])

    assert _bootstrap(project) == odd
    assert "by hand" in result.output


# -- GEN-02 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("hunt", framework_requirement()),
        ("hunt>=0.1", framework_requirement()),
        ("hunt ==0.0", framework_requirement()),
        ("Hunt[redis]", framework_requirement().replace("hunt-framework", "hunt-framework[redis]")),
        ('hunt; python_version >= "3.11"', framework_requirement() + ' ; python_version >= "3.11"'),
        ("hunt-framework>=0.4,<0.5", None),
        ("hunt_framework", None),
        ("hunter", None),
        ("hunt.ext", None),
        ("pytest-hunt", None),
    ],
)
def test_rewrite_requirement(requirement, expected):
    assert _rewrite_requirement(requirement) == expected


def test_plan_pyproject_only_touches_dependency_items():
    new_text, replacements = _plan_pyproject(_PYPROJECT)

    data = tomllib.loads(new_text)
    assert data["project"]["name"] == "hunt"
    assert data["project"]["dependencies"] == [framework_requirement(), "httpx>=0.27"]
    assert data["project"]["optional-dependencies"]["dev"] == ["pytest>=8.0", framework_requirement()]
    assert [old for old, _ in replacements] == ["hunt", "hunt>=0.1"]


def test_upgrade_rewrites_bare_hunt_dependency_with_yes(project):
    (project / "pyproject.toml").write_text(_PYPROJECT)

    result = CliRunner().invoke(upgrade_command, ["--yes"])

    assert result.exit_code == 0
    data = tomllib.loads((project / "pyproject.toml").read_text())
    assert framework_requirement() in data["project"]["dependencies"]
    assert "hunt" not in data["project"]["dependencies"]
    assert "pyproject.toml patched" in result.output


def test_upgrade_does_not_rewrite_dependency_without_confirmation(project):
    (project / "pyproject.toml").write_text(_PYPROJECT)

    result = CliRunner().invoke(upgrade_command, [])

    assert (project / "pyproject.toml").read_text() == _PYPROJECT
    assert "unrelated package" in result.output


def test_upgrade_leaves_correct_dependency_alone(project):
    good = _PYPROJECT.replace('    "hunt",\n', f'    "{framework_requirement()}",\n').replace(", 'hunt>=0.1'", "")
    (project / "pyproject.toml").write_text(good)

    result = CliRunner().invoke(upgrade_command, ["--yes"])

    assert (project / "pyproject.toml").read_text() == good
    assert "unrelated package" not in result.output
