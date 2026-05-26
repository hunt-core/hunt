from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# MediaBrowser field
# ---------------------------------------------------------------------------


class TestMediaBrowserField:
    def test_field_type(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Gallery")
        assert f.field_type == "media_browser"

    def test_hidden_from_index(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Gallery")
        assert f._show_on_index is False

    def test_hidden_from_detail(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Gallery")
        assert f._show_on_detail is False

    def test_shown_on_create(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Gallery")
        assert f._show_on_create is True

    def test_shown_on_edit(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Gallery")
        assert f._show_on_edit is True

    def test_value_for_returns_none(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Gallery")
        instance = MagicMock()
        instance._attributes = {"gallery": "some_value"}
        assert f.value_for(instance) is None

    def test_exported_from_fields_package(self):
        from hunt.admin.fields import MediaBrowser

        assert MediaBrowser is not None

    def test_in_all_list(self):
        import hunt.admin.fields as pkg

        assert "MediaBrowser" in pkg.__all__

    def test_label_set_from_name(self):
        from hunt.admin.fields.media_browser import MediaBrowser

        f = MediaBrowser("Media Files")
        assert f.label == "Media Files"


# ---------------------------------------------------------------------------
# Admin singleton — media_disk / media_path attributes
# ---------------------------------------------------------------------------


class TestAdminMediaConfig:
    def test_default_media_disk(self):
        from hunt.admin.application import Admin

        assert Admin.media_disk == "public"

    def test_default_media_path(self):
        from hunt.admin.application import Admin

        assert Admin.media_path == "media"

    def test_media_disk_configurable(self):
        from hunt.admin.application import _Admin

        a = _Admin()
        a.media_disk = "s3"
        assert a.media_disk == "s3"

    def test_media_path_configurable(self):
        from hunt.admin.application import _Admin

        a = _Admin()
        a.media_path = "uploads/images"
        assert a.media_path == "uploads/images"


# ---------------------------------------------------------------------------
# Media controller
# ---------------------------------------------------------------------------


def _make_request(**kwargs):
    req = MagicMock()
    req.input = MagicMock(side_effect=lambda k, default=None: kwargs.get(k, default))
    req.file = MagicMock(return_value=None)
    req._session = None
    return req


class TestMediaController:
    def test_api_list_empty(self):
        from hunt.admin.controllers.media import api_list

        mock_disk = MagicMock()
        mock_disk.files.return_value = []
        with patch("hunt.admin.controllers.media._disk", return_value=mock_disk):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                resp = api_list(_make_request())
        import json

        data = json.loads(resp._body)
        assert data == {"files": []}

    def test_api_list_returns_files(self):
        from hunt.admin.controllers.media import api_list

        mock_disk = MagicMock()
        mock_disk.files.return_value = ["media/foo.jpg", "media/bar.png"]
        mock_disk.url.side_effect = lambda p: f"/storage/{p}"
        mock_disk.size.return_value = 1024
        with patch("hunt.admin.controllers.media._disk", return_value=mock_disk):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                resp = api_list(_make_request())
        import json

        data = json.loads(resp._body)
        assert len(data["files"]) == 2
        assert data["files"][0]["name"] in ("bar.png", "foo.jpg")

    def test_api_list_disk_error_returns_empty(self):
        from hunt.admin.controllers.media import api_list

        mock_disk = MagicMock()
        mock_disk.files.side_effect = RuntimeError("disk not configured")
        with patch("hunt.admin.controllers.media._disk", return_value=mock_disk):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                resp = api_list(_make_request())
        import json

        data = json.loads(resp._body)
        assert data["files"] == []

    def test_upload_no_file_raises(self):
        from hunt.admin.controllers.media import upload
        from hunt.http.response import HttpException

        req = _make_request()
        req.file.return_value = None
        with patch("hunt.admin.controllers.media._disk"):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                try:
                    upload(req)
                    raise AssertionError("expected HttpException")
                except HttpException as e:
                    assert e.status == 422

    def test_upload_invalid_mime_raises(self):
        from hunt.admin.controllers.media import upload
        from hunt.http.response import HttpException

        mock_file = MagicMock()
        mock_file.get_mime_type.return_value = "application/pdf"
        req = _make_request()
        req.file.return_value = mock_file
        with patch("hunt.admin.controllers.media._disk"):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                try:
                    upload(req)
                    raise AssertionError("expected HttpException")
                except HttpException as e:
                    assert e.status == 422

    def test_upload_valid_image(self):
        from hunt.admin.controllers.media import upload

        mock_file = MagicMock()
        mock_file.get_mime_type.return_value = "image/png"
        mock_disk = MagicMock()
        mock_disk.put_file.return_value = "media/abc123.png"
        mock_disk.url.return_value = "/storage/media/abc123.png"
        req = _make_request()
        req.file.return_value = mock_file
        with patch("hunt.admin.controllers.media._disk", return_value=mock_disk):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                resp = upload(req)
        import json

        data = json.loads(resp._body)
        assert data["url"] == "/storage/media/abc123.png"
        assert data["name"] == "abc123.png"
        assert resp.status == 201

    def test_delete_no_path_raises(self):
        from hunt.admin.controllers.media import delete
        from hunt.http.response import HttpException

        req = _make_request()
        with patch("hunt.admin.controllers.media._disk"):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                try:
                    delete(req)
                    raise AssertionError("expected HttpException")
                except HttpException as e:
                    assert e.status == 422

    def test_delete_path_outside_media_forbidden(self):
        from hunt.admin.controllers.media import delete
        from hunt.http.response import HttpException

        req = _make_request(path="other/evil.jpg")
        with patch("hunt.admin.controllers.media._disk"):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                try:
                    delete(req)
                    raise AssertionError("expected HttpException")
                except HttpException as e:
                    assert e.status == 403

    def test_delete_valid_path(self):
        from hunt.admin.controllers.media import delete

        mock_disk = MagicMock()
        req = _make_request(path="media/foo.jpg")
        with patch("hunt.admin.controllers.media._disk", return_value=mock_disk):
            with patch("hunt.admin.controllers.media._path", return_value="media"):
                resp = delete(req)
        import json

        data = json.loads(resp._body)
        assert data["deleted"] is True
        mock_disk.delete.assert_called_once_with("media/foo.jpg")

    def test_allowed_mime_types(self):
        from hunt.admin.controllers.media import _ALLOWED_MIME

        assert "image/jpeg" in _ALLOWED_MIME
        assert "image/png" in _ALLOWED_MIME
        assert "image/gif" in _ALLOWED_MIME
        assert "image/webp" in _ALLOWED_MIME
        assert "image/svg+xml" in _ALLOWED_MIME
        assert "application/pdf" not in _ALLOWED_MIME
