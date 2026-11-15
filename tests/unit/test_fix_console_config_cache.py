"""Regression tests for GEN-10: config:cache writes a private file that the application loads."""

import json
import os
import stat
import sys

import pytest
from click.testing import CliRunner

from hunt.config.loader import config_cache_path, load_config_cache, load_config_directory
from hunt.console.commands.config_cache import _load_config_dir, config_cache_command, config_clear_command

posix_only = pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership/mode checks")

_APP_CONFIG = """\
import os

config = {
    "name": os.environ.get("FIX_CONSOLE_APP_NAME", "from-file"),
    "debug": False,
    "ports": [80, 443],
    "nested": {"secret": os.environ.get("FIX_CONSOLE_SECRET"), "ratio": 0.5},
}
"""


@pytest.fixture()
def project(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "app.py").write_text(_APP_CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delenv("FIX_CONSOLE_APP_NAME", raising=False)
    monkeypatch.delenv("FIX_CONSOLE_SECRET", raising=False)
    return tmp_path


def _cache(project):
    return project / "storage" / "framework" / "config.json"


def test_cache_path_is_under_storage_framework(project):
    assert config_cache_path(project / "config") == _cache(project)


@posix_only
def test_cache_file_is_private_and_leaves_no_temp_file(project):
    old_umask = os.umask(0o000)
    try:
        result = CliRunner().invoke(config_cache_command, [])
    finally:
        os.umask(old_umask)

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(_cache(project).stat().st_mode) == 0o600
    assert [p.name for p in _cache(project).parent.iterdir()] == ["config.json"]


def test_loader_returns_cached_values_instead_of_executing_config_files(project, monkeypatch):
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "cached-name")
    monkeypatch.setenv("FIX_CONSOLE_SECRET", "s3cr3t")
    assert CliRunner().invoke(config_cache_command, []).exit_code == 0

    # The environment changes after caching; the cache wins until it is cleared.
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "live-name")
    (project / "config" / "app.py").write_text("raise RuntimeError('config files must not run when cached')\n")

    loaded = load_config_directory(project / "config")
    assert loaded == {
        "app": {"name": "cached-name", "debug": False, "ports": [80, 443], "nested": {"secret": "s3cr3t", "ratio": 0.5}}
    }


def test_cached_shape_matches_uncached_shape(project):
    uncached = load_config_directory(project / "config")
    assert CliRunner().invoke(config_cache_command, []).exit_code == 0
    assert load_config_cache(_cache(project)) == uncached
    assert load_config_directory(project / "config") == uncached


def test_application_boots_from_cache(project, monkeypatch):
    from hunt.application import Application

    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "cached-app")
    assert CliRunner().invoke(config_cache_command, []).exit_code == 0
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "live-app")

    app = Application(project)
    assert app.make("config").get("app.name") == "cached-app"

    assert CliRunner().invoke(config_clear_command, []).exit_code == 0
    assert not _cache(project).exists()
    app = Application(project)
    assert app.make("config").get("app.name") == "live-app"


def test_recaching_reads_config_files_not_the_stale_cache(project, monkeypatch):
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "first")
    CliRunner().invoke(config_cache_command, [])
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "second")
    CliRunner().invoke(config_cache_command, [])

    assert json.loads(_cache(project).read_text())["app"]["name"] == "second"


def test_use_cache_false_bypasses_cache(project, monkeypatch):
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "cached")
    CliRunner().invoke(config_cache_command, [])
    monkeypatch.setenv("FIX_CONSOLE_APP_NAME", "live")

    assert load_config_directory(project / "config", use_cache=False)["app"]["name"] == "live"


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nconfig = {'root': Path('/srv/app')}\n",
        "config = {'hosts': ('a', 'b')}\n",
        "config = {'tags': {'a', 'b'}}\n",
        "config = {1: 'one'}\n",
        "config = {'nan': float('nan')}\n",
        "import os\nname = 'no config dict, so the os module is exported too'\n",
    ],
)
def test_refuses_values_json_cannot_represent(project, source):
    (project / "config" / "extra.py").write_text(source)

    result = CliRunner().invoke(config_cache_command, [])

    assert result.exit_code == 1
    assert "not cached" in result.output
    assert "extra" in result.output
    assert not _cache(project).exists()


def test_refusal_keeps_previous_cache_untouched(project):
    assert CliRunner().invoke(config_cache_command, []).exit_code == 0
    before = _cache(project).read_bytes()
    (project / "config" / "extra.py").write_text("config = {'hosts': ('a', 'b')}\n")

    assert CliRunner().invoke(config_cache_command, []).exit_code == 1
    assert _cache(project).read_bytes() == before


def test_config_import_error_is_surfaced(project):
    (project / "config" / "broken.py").write_text("raise RuntimeError('boom in config')\n")

    result = CliRunner().invoke(config_cache_command, [])

    assert result.exit_code == 1
    assert "boom in config" in result.output
    assert not _cache(project).exists()


def test_load_config_dir_names_the_failing_file(project):
    import click

    (project / "config" / "broken.py").write_text("import module_that_does_not_exist_xyz\n")

    with pytest.raises(click.ClickException) as excinfo:
        _load_config_dir(project / "config")
    assert "config/broken.py" in excinfo.value.message


# -- loader trust checks ----------------------------------------------------


def _write_cache(project, text, mode=0o600):
    cache = _cache(project)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text)
    cache.chmod(mode)
    return cache


@pytest.mark.parametrize("text", ["{not json", "[1, 2, 3]", '"a string"', "null", ""])
def test_loader_ignores_unparsable_or_non_object_cache(project, text):
    _write_cache(project, text)

    assert load_config_cache(_cache(project)) is None
    assert load_config_directory(project / "config")["app"]["name"] == "from-file"


@posix_only
@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_loader_ignores_group_or_world_writable_cache(project, mode):
    _write_cache(project, json.dumps({"app": {"name": "planted"}}), mode=mode)

    assert load_config_cache(_cache(project)) is None
    assert load_config_directory(project / "config")["app"]["name"] == "from-file"


@posix_only
def test_loader_ignores_symlinked_cache(project):
    real = project / "elsewhere.json"
    real.write_text(json.dumps({"app": {"name": "planted"}}))
    real.chmod(0o600)
    _cache(project).parent.mkdir(parents=True)
    _cache(project).symlink_to(real)

    assert load_config_cache(_cache(project)) is None
    assert load_config_directory(project / "config")["app"]["name"] == "from-file"


@posix_only
def test_loader_ignores_cache_owned_by_another_user(project, monkeypatch):
    _write_cache(project, json.dumps({"app": {"name": "planted"}}))
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)

    assert load_config_cache(_cache(project)) is None


def test_loader_ignores_directory_in_place_of_cache(project):
    _cache(project).mkdir(parents=True)

    assert load_config_cache(_cache(project)) is None


def test_loader_accepts_valid_private_cache(project):
    _write_cache(project, json.dumps({"app": {"name": "trusted"}}))

    assert load_config_directory(project / "config") == {"app": {"name": "trusted"}}


@posix_only
def test_config_clear_removes_dangling_symlink(project):
    _cache(project).parent.mkdir(parents=True)
    _cache(project).symlink_to(project / "missing.json")

    result = CliRunner().invoke(config_clear_command, [])

    assert "cleared" in result.output
    assert not _cache(project).is_symlink()
