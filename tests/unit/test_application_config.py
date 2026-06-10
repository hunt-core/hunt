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
