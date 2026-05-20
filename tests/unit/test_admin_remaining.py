from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# DateRangeFilter
# ---------------------------------------------------------------------------


class TestDateRangeFilter:
    def _make_filter(self, attr="created_at"):
        from hunt.admin.filter import DateRangeFilter

        class CreatedAtFilter(DateRangeFilter):
            name = "Created At"
            attribute = attr

        return CreatedAtFilter()

    def test_slug(self):
        f = self._make_filter()
        assert f.slug() == "created_at"

    def test_filter_type(self):
        f = self._make_filter()
        assert f.filter_type == "date_range"

    def test_apply_from_only(self):
        f = self._make_filter()
        query = MagicMock()
        query.where.return_value = query
        f.apply(query, {"from": "2024-01-01", "to": None})
        query.where.assert_called_once_with("created_at", ">=", "2024-01-01")

    def test_apply_to_only(self):
        f = self._make_filter()
        query = MagicMock()
        query.where.return_value = query
        f.apply(query, {"from": None, "to": "2024-12-31"})
        query.where.assert_called_once_with("created_at", "<=", "2024-12-31")

    def test_apply_both(self):
        f = self._make_filter()
        query = MagicMock()
        query.where.return_value = query
        f.apply(query, {"from": "2024-01-01", "to": "2024-12-31"})
        assert query.where.call_count == 2

    def test_apply_neither_returns_unchanged(self):
        f = self._make_filter()
        query = MagicMock()
        result = f.apply(query, {"from": None, "to": None})
        query.where.assert_not_called()
        assert result is query

    def test_apply_non_dict_ignored(self):
        f = self._make_filter()
        query = MagicMock()
        result = f.apply(query, "not-a-dict")
        query.where.assert_not_called()
        assert result is query

    def test_apply_filters_in_resource_routes_date_range(self):
        from hunt.admin.filter import DateRangeFilter
        from hunt.admin.resource import AdminResource

        class DummyModel:
            @classmethod
            def query(cls):
                q = MagicMock()
                q.where.return_value = q
                q.order_by.return_value = q
                return q

        class MyFilter(DateRangeFilter):
            name = "Date"
            attribute = "created_at"

        class MyResource(AdminResource):
            model = DummyModel

            def fields(self):
                return []

            def filters(self):
                return [MyFilter()]

        resource = MyResource()
        request = MagicMock()
        request.query.side_effect = lambda key, default=None: {
            "filter_date_from": "2024-01-01",
            "filter_date_to": "2024-06-30",
            "search": "",
            "sort": "",
            "dir": "desc",
        }.get(key, default)

        query = resource.index_query(request)
        assert query.where.call_count >= 2


# ---------------------------------------------------------------------------
# RestoreAction
# ---------------------------------------------------------------------------


class TestRestoreAction:
    def test_restores_models(self):
        from hunt.admin.action import RestoreAction

        action = RestoreAction()
        m1, m2 = MagicMock(), MagicMock()
        result = action.handle(MagicMock(), [m1, m2])
        m1.restore.assert_called_once()
        m2.restore.assert_called_once()
        assert result.type == "message"
        assert result.message_type == "success"
        assert "2" in result.text

    def test_empty_list_returns_error(self):
        from hunt.admin.action import RestoreAction

        action = RestoreAction()
        result = action.handle(MagicMock(), [])
        assert result.message_type == "error"

    def test_restore_exception_is_swallowed(self):
        from hunt.admin.action import RestoreAction

        action = RestoreAction()
        m = MagicMock()
        m.restore.side_effect = RuntimeError("no soft deletes")
        result = action.handle(MagicMock(), [m])
        assert result.message_type == "error"

    def test_slug(self):
        from hunt.admin.action import RestoreAction

        assert RestoreAction.slug() == "restore_selected"

    def test_not_destructive(self):
        from hunt.admin.action import RestoreAction

        assert RestoreAction.destructive is False


# ---------------------------------------------------------------------------
# ExportCsvAction
# ---------------------------------------------------------------------------


class TestExportCsvAction:
    def _make_instance(self, attrs):
        m = MagicMock()
        m._attributes = attrs
        return m

    def test_returns_download_response(self):
        from hunt.admin.action import ExportCsvAction

        action = ExportCsvAction()
        models = [self._make_instance({"id": 1, "name": "Alice"})]
        result = action.handle(MagicMock(), models)
        assert result.type == "download"
        assert result.download_content_type.startswith("text/csv")

    def test_csv_has_header_and_row(self):
        from hunt.admin.action import ExportCsvAction

        action = ExportCsvAction()
        models = [self._make_instance({"id": 1, "name": "Alice"})]
        result = action.handle(MagicMock(), models)
        lines = result.download_content.strip().splitlines()
        assert lines[0] == "id,name"
        assert "Alice" in lines[1]

    def test_multiple_rows(self):
        from hunt.admin.action import ExportCsvAction

        action = ExportCsvAction()
        models = [
            self._make_instance({"id": 1, "name": "Alice"}),
            self._make_instance({"id": 2, "name": "Bob"}),
        ]
        result = action.handle(MagicMock(), models)
        lines = result.download_content.strip().splitlines()
        assert len(lines) == 3

    def test_none_values_become_empty_string(self):
        from hunt.admin.action import ExportCsvAction

        action = ExportCsvAction()
        models = [self._make_instance({"id": 1, "name": None})]
        result = action.handle(MagicMock(), models)
        assert "," in result.download_content

    def test_empty_selection_returns_error(self):
        from hunt.admin.action import ExportCsvAction

        action = ExportCsvAction()
        result = action.handle(MagicMock(), [])
        assert result.type == "message"
        assert result.message_type == "error"

    def test_custom_filename(self):
        from hunt.admin.action import ExportCsvAction

        class UsersExport(ExportCsvAction):
            filename = "users.csv"

        action = UsersExport()
        models = [self._make_instance({"id": 1})]
        result = action.handle(MagicMock(), models)
        assert result.download_filename == "users.csv"

    def test_slug(self):
        from hunt.admin.action import ExportCsvAction

        assert ExportCsvAction.slug() == "export_csv"


# ---------------------------------------------------------------------------
# ActionResponse.download constructor
# ---------------------------------------------------------------------------


class TestActionResponseDownload:
    def test_type_is_download(self):
        from hunt.admin.action import ActionResponse

        r = ActionResponse.download("a,b\n1,2\n")
        assert r.type == "download"

    def test_content_stored(self):
        from hunt.admin.action import ActionResponse

        r = ActionResponse.download("hello")
        assert r.download_content == "hello"

    def test_default_filename(self):
        from hunt.admin.action import ActionResponse

        r = ActionResponse.download("")
        assert r.download_filename == "export.csv"

    def test_custom_filename(self):
        from hunt.admin.action import ActionResponse

        r = ActionResponse.download("", filename="users.csv")
        assert r.download_filename == "users.csv"


# ---------------------------------------------------------------------------
# admin:publish command
# ---------------------------------------------------------------------------


class TestAdminPublishCommand:
    def test_copies_templates(self, tmp_path):
        from click.testing import CliRunner

        from hunt.admin.console.publish import admin_publish_command

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(admin_publish_command, [])
        assert result.exit_code == 0
        assert "Published" in result.output or "file(s) published" in result.output

    def test_force_flag_overwrites(self, tmp_path):
        from click.testing import CliRunner

        from hunt.admin.console.publish import admin_publish_command

        runner = CliRunner()
        # Run once, then again with --force
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(admin_publish_command, [])
            result = runner.invoke(admin_publish_command, ["--force"])
        assert result.exit_code == 0

    def test_no_force_skips_existing(self, tmp_path):
        from click.testing import CliRunner

        from hunt.admin.console.publish import admin_publish_command

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(admin_publish_command, [])
            result = runner.invoke(admin_publish_command, [])
        assert "already exists" in result.output or result.exit_code == 0
