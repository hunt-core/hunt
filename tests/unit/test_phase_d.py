"""Phase D — Storage / Filesystem tests."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIME_EXT = {
    "text/plain": ".txt",
    "image/jpeg": ".jpg",
    "application/pdf": ".pdf",
}


@dataclass
class _FakeFile:
    filename: str
    content_type: str = "text/plain"
    _data: bytes = field(default=b"hello", repr=False)

    @property
    def content(self) -> bytes:
        return self._data

    def get_mime_type(self) -> str:
        return self.content_type


# ---------------------------------------------------------------------------
# LocalDisk
# ---------------------------------------------------------------------------


class TestLocalDiskPutGet:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_put_and_get_bytes(self):
        self.disk.put("a.txt", b"hello bytes")
        assert self.disk.get("a.txt") == b"hello bytes"

    def test_put_and_get_str(self):
        self.disk.put("b.txt", "hello text")
        assert self.disk.get_text("b.txt") == "hello text"

    def test_put_creates_parent_dirs(self):
        self.disk.put("nested/deep/c.txt", b"deep")
        assert (Path(self._tmp) / "nested" / "deep" / "c.txt").exists()

    def test_exists_and_missing(self):
        self.disk.put("x.txt", b"x")
        assert self.disk.exists("x.txt") is True
        assert self.disk.missing("x.txt") is False
        assert self.disk.exists("nope.txt") is False
        assert self.disk.missing("nope.txt") is True


class TestLocalDiskDelete:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_delete_single(self):
        self.disk.put("del.txt", b"bye")
        assert self.disk.delete("del.txt") is True
        assert self.disk.exists("del.txt") is False

    def test_delete_nonexistent_returns_true(self):
        assert self.disk.delete("ghost.txt") is True

    def test_delete_list(self):
        self.disk.put("a.txt", b"a")
        self.disk.put("b.txt", b"b")
        self.disk.delete(["a.txt", "b.txt"])
        assert self.disk.missing("a.txt")
        assert self.disk.missing("b.txt")


class TestLocalDiskCopyMove:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_copy(self):
        self.disk.put("src.txt", b"data")
        self.disk.copy("src.txt", "dst.txt")
        assert self.disk.get("dst.txt") == b"data"
        assert self.disk.exists("src.txt")

    def test_move(self):
        self.disk.put("mv_src.txt", b"moveme")
        self.disk.move("mv_src.txt", "mv_dst.txt")
        assert self.disk.get("mv_dst.txt") == b"moveme"
        assert self.disk.missing("mv_src.txt")


class TestLocalDiskListing:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_files_non_recursive(self):
        self.disk.put("f1.txt", b"1")
        self.disk.put("f2.txt", b"2")
        self.disk.put("sub/f3.txt", b"3")
        files = self.disk.files()
        assert "f1.txt" in files
        assert "f2.txt" in files
        assert not any("f3" in f for f in files)

    def test_all_files_recursive(self):
        self.disk.put("r1.txt", b"r1")
        self.disk.put("sub/r2.txt", b"r2")
        self.disk.put("sub/deep/r3.txt", b"r3")
        files = self.disk.all_files()
        names = [Path(f).name for f in files]
        assert "r1.txt" in names
        assert "r2.txt" in names
        assert "r3.txt" in names

    def test_directories(self):
        self.disk.make_directory("dirA")
        self.disk.make_directory("dirB")
        dirs = self.disk.directories()
        assert "dirA" in dirs
        assert "dirB" in dirs

    def test_files_in_subdirectory(self):
        self.disk.put("imgs/a.png", b"imgdata")
        files = self.disk.files("imgs")
        assert any("a.png" in f for f in files)

    def test_files_empty_dir(self):
        self.disk.make_directory("empty_dir")
        assert self.disk.files("empty_dir") == []


class TestLocalDiskMetadata:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_size(self):
        self.disk.put("s.txt", b"12345")
        assert self.disk.size("s.txt") == 5

    def test_last_modified(self):
        self.disk.put("m.txt", b"ts")
        ts = self.disk.last_modified("m.txt")
        assert isinstance(ts, int)
        assert ts > 0

    def test_mime_type_text(self):
        self.disk.put("doc.txt", b"text")
        mt = self.disk.mime_type("doc.txt")
        assert "text" in mt

    def test_mime_type_unknown(self):
        self.disk.put("file.xyzunknown", b"data")
        mt = self.disk.mime_type("file.xyzunknown")
        assert mt == "application/octet-stream"


class TestLocalDiskUrl:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()

    def test_url_no_prefix(self):
        from hunt.storage.local import LocalDisk

        disk = LocalDisk(self._tmp)
        assert disk.url("images/photo.jpg") == "/storage/images/photo.jpg"

    def test_url_with_prefix(self):
        from hunt.storage.local import LocalDisk

        disk = LocalDisk(self._tmp, url_prefix="https://cdn.example.com")
        assert disk.url("images/photo.jpg") == "https://cdn.example.com/images/photo.jpg"

    def test_url_strips_leading_slash(self):
        from hunt.storage.local import LocalDisk

        disk = LocalDisk(self._tmp, url_prefix="https://cdn.example.com")
        assert disk.url("/images/photo.jpg") == "https://cdn.example.com/images/photo.jpg"


class TestLocalDiskAppendPrepend:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_append_str(self):
        self.disk.put("log.txt", "line1\n")
        self.disk.append("log.txt", "line2\n")
        assert self.disk.get_text("log.txt") == "line1\nline2\n"

    def test_append_bytes(self):
        self.disk.put("bin.dat", b"\x01")
        self.disk.append("bin.dat", b"\x02")
        assert self.disk.get("bin.dat") == b"\x01\x02"

    def test_prepend(self):
        self.disk.put("pre.txt", b"world")
        self.disk.prepend("pre.txt", b"hello ")
        assert self.disk.get("pre.txt") == b"hello world"


class TestLocalDiskPutFile:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_put_file_stores_content(self):
        f = _FakeFile(filename="upload.txt", _data=b"uploaded content")
        stored = self.disk.put_file("uploads", f)
        assert stored.startswith("uploads/")
        assert stored.endswith(".txt")
        assert self.disk.get(stored) == b"uploaded content"

    def test_put_file_custom_name(self):
        f = _FakeFile(filename="original.txt", _data=b"renamed")
        stored = self.disk.put_file("uploads", f, name="custom.txt")
        assert stored == "uploads/custom.txt"


class TestLocalDiskDirectoryOps:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.local import LocalDisk

        self.disk = LocalDisk(self._tmp)

    def test_make_directory(self):
        self.disk.make_directory("newdir/nested")
        assert (Path(self._tmp) / "newdir" / "nested").is_dir()

    def test_delete_directory_removes_contents(self):
        self.disk.put("rmdir/a.txt", b"a")
        self.disk.put("rmdir/b/c.txt", b"c")
        self.disk.delete_directory("rmdir")
        assert not (Path(self._tmp) / "rmdir").exists()

    def test_delete_directory_nonexistent_ok(self):
        assert self.disk.delete_directory("ghost_dir") is True

    def test_path_returns_absolute(self):
        result = self.disk.path("some/file.txt")
        assert result == str(Path(self._tmp) / "some" / "file.txt")

    def test_path_no_arg(self):
        result = self.disk.path()
        assert result == str(Path(self._tmp))


# ---------------------------------------------------------------------------
# S3Disk
# ---------------------------------------------------------------------------


class TestS3DiskInterface:
    """S3Disk with mocked boto3 client."""

    def setup_method(self):
        self._mock_client = MagicMock()
        from hunt.storage.s3 import S3Disk

        self.disk = S3Disk(
            {
                "bucket": "my-bucket",
                "region": "us-east-1",
                "key": "AKID",
                "secret": "SECRET",
            }
        )
        self.disk._client = self._mock_client

    def test_put_bytes(self):
        self.disk.put("path/file.txt", b"data")
        self._mock_client.put_object.assert_called_once_with(Bucket="my-bucket", Key="path/file.txt", Body=b"data")

    def test_put_str_encodes(self):
        self.disk.put("f.txt", "text")
        call_kwargs = self._mock_client.put_object.call_args[1]
        assert call_kwargs["Body"] == b"text"

    def test_get(self):
        self._mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"content")}
        result = self.disk.get("file.txt")
        assert result == b"content"

    def test_get_text(self):
        self._mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"text content")}
        assert self.disk.get_text("f.txt") == "text content"

    def test_exists_true(self):
        assert self.disk.exists("present.txt") is True

    def test_exists_false(self):
        self._mock_client.head_object.side_effect = Exception("NoSuchKey")
        assert self.disk.exists("absent.txt") is False

    def test_missing(self):
        self._mock_client.head_object.side_effect = Exception("NoSuchKey")
        assert self.disk.missing("absent.txt") is True

    def test_delete_single(self):
        self.disk.delete("f.txt")
        self._mock_client.delete_objects.assert_called_once_with(
            Bucket="my-bucket", Delete={"Objects": [{"Key": "f.txt"}]}
        )

    def test_delete_list(self):
        self.disk.delete(["a.txt", "b.txt"])
        call_kwargs = self._mock_client.delete_objects.call_args[1]
        keys = [o["Key"] for o in call_kwargs["Delete"]["Objects"]]
        assert "a.txt" in keys and "b.txt" in keys

    def test_copy(self):
        self.disk.copy("src.txt", "dst.txt")
        self._mock_client.copy_object.assert_called_once_with(
            Bucket="my-bucket",
            CopySource={"Bucket": "my-bucket", "Key": "src.txt"},
            Key="dst.txt",
        )

    def test_move_copies_then_deletes(self):
        self.disk.move("src.txt", "dst.txt")
        assert self._mock_client.copy_object.called
        assert self._mock_client.delete_objects.called

    def test_size(self):
        self._mock_client.head_object.return_value = {
            "ContentLength": 42,
            "LastModified": MagicMock(timestamp=lambda: 1000.0),
        }
        assert self.disk.size("f.txt") == 42

    def test_last_modified(self):
        self._mock_client.head_object.return_value = {
            "ContentLength": 0,
            "LastModified": MagicMock(timestamp=lambda: 9999.5),
        }
        assert self.disk.last_modified("f.txt") == 9999

    def test_url_with_prefix(self):
        from hunt.storage.s3 import S3Disk

        disk = S3Disk({"bucket": "b", "url": "https://cdn.example.com", "key": "", "secret": ""})
        disk._client = self._mock_client
        assert disk.url("img/photo.jpg") == "https://cdn.example.com/img/photo.jpg"

    def test_url_default_aws(self):
        assert self.disk.url("img/photo.jpg") == "https://my-bucket.s3.us-east-1.amazonaws.com/img/photo.jpg"

    def test_path(self):
        assert self.disk.path("dir/file.txt") == "s3://my-bucket/dir/file.txt"

    def test_make_directory(self):
        self.disk.make_directory("uploads")
        self._mock_client.put_object.assert_called_once_with(Bucket="my-bucket", Key="uploads/", Body=b"")

    def test_files_non_recursive(self):
        self._mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "dir/a.txt"}, {"Key": "dir/"}, {"Key": "dir/b.txt"}]
        }
        files = self.disk.files("dir")
        assert "dir/a.txt" in files
        assert "dir/b.txt" in files
        assert "dir/" not in files

    def test_all_files_paginated(self):
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "a.txt"}, {"Key": "b.txt"}]},
            {"Contents": [{"Key": "c.txt"}]},
        ]
        self._mock_client.get_paginator.return_value = paginator
        files = self.disk.all_files()
        assert set(files) == {"a.txt", "b.txt", "c.txt"}

    def test_put_file_generates_unique_name(self):
        # Parity with LocalDisk: with no explicit name a UUID name is generated
        # rather than reusing the raw client filename (avoids overwrites and
        # unpredictable, guessable keys). The extension is kept only because the
        # content type matches it.
        f = _FakeFile(filename="upload.jpg", content_type="image/jpeg", _data=b"imgdata")
        stored = self.disk.put_file("photos", f)
        assert stored.startswith("photos/")
        assert stored != "photos/upload.jpg"
        assert stored.endswith(".jpg")
        self._mock_client.put_object.assert_called_once()
        assert self._mock_client.put_object.call_args[1]["Key"] == stored

    def test_put_file_explicit_name_uses_basename(self):
        # An explicit name is reduced to its basename so it cannot escape the
        # target directory (same as LocalDisk).
        f = _FakeFile(filename="upload.jpg", _data=b"imgdata")
        stored = self.disk.put_file("photos", f, name="a/b/avatar.png")
        assert stored == "photos/avatar.png"

    def test_mime_type(self):
        assert self.disk.mime_type("dir/photo.png") == "image/png"

    def test_directories(self):
        self._mock_client.list_objects_v2.return_value = {
            "CommonPrefixes": [{"Prefix": "dir/sub1/"}, {"Prefix": "dir/sub2/"}]
        }
        assert self.disk.directories("dir") == ["dir/sub1", "dir/sub2"]

    def test_append_read_modify_write(self):
        self._mock_client.head_object.return_value = {
            "ContentLength": 3,
            "LastModified": MagicMock(timestamp=lambda: 0.0),
        }
        self._mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"abc")}
        self.disk.append("f.txt", "def")
        assert self._mock_client.put_object.call_args[1]["Body"] == b"abcdef"

    def test_prepend_read_modify_write(self):
        self._mock_client.head_object.return_value = {
            "ContentLength": 3,
            "LastModified": MagicMock(timestamp=lambda: 0.0),
        }
        self._mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: b"abc")}
        self.disk.prepend("f.txt", "def")
        assert self._mock_client.put_object.call_args[1]["Body"] == b"defabc"

    def test_append_when_missing_creates(self):
        self._mock_client.head_object.side_effect = Exception("NoSuchKey")
        self.disk.append("new.txt", b"data")
        assert self._mock_client.put_object.call_args[1]["Body"] == b"data"

    def test_delete_directory(self):
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "dir/a.txt"}]},
        ]
        self._mock_client.get_paginator.return_value = paginator
        self.disk.delete_directory("dir")
        assert self._mock_client.delete_objects.called

    def test_boto3_missing_raises(self):
        from hunt.storage.s3 import S3Disk

        disk = S3Disk({"bucket": "b", "key": "", "secret": ""})
        with patch.dict("sys.modules", {"boto3": None}):
            with pytest.raises(RuntimeError, match="boto3"):
                disk._boto()

    def test_endpoint_url_passed_to_boto3(self):
        """MinIO / custom S3 endpoint is forwarded."""
        import sys

        from hunt.storage.s3 import S3Disk

        disk = S3Disk(
            {
                "bucket": "b",
                "key": "k",
                "secret": "s",
                "endpoint": "http://localhost:9000",
            }
        )
        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = MagicMock()
        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            disk._boto()
        call_kwargs = mock_boto3.client.call_args[1]
        assert call_kwargs.get("endpoint_url") == "http://localhost:9000"


# ---------------------------------------------------------------------------
# StorageManager
# ---------------------------------------------------------------------------


class TestStorageManager:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.manager import _StorageManager

        self.mgr = _StorageManager()
        self.mgr.configure(
            {
                "default": "local",
                "disks": {
                    "local": {"driver": "local", "root": self._tmp},
                    "public": {"driver": "local", "root": self._tmp + "/public"},
                },
            }
        )

    def test_disk_returns_local_disk(self):
        from hunt.storage.local import LocalDisk

        assert isinstance(self.mgr.disk("local"), LocalDisk)

    def test_disk_returns_default(self):
        from hunt.storage.local import LocalDisk

        assert isinstance(self.mgr.disk(), LocalDisk)

    def test_disk_caches_instance(self):
        d1 = self.mgr.disk("local")
        d2 = self.mgr.disk("local")
        assert d1 is d2

    def test_disk_unknown_raises(self):
        with pytest.raises(RuntimeError, match="not configured"):
            self.mgr.disk("unknown")

    def test_disk_bad_driver_raises(self):
        self.mgr._config["disks"]["bad"] = {"driver": "ftp"}
        with pytest.raises(RuntimeError, match="Unsupported storage driver"):
            self.mgr.disk("bad")

    def test_put_and_get_via_manager(self):
        self.mgr.put("mgr_test.txt", b"via manager")
        assert self.mgr.get("mgr_test.txt") == b"via manager"

    def test_exists_via_manager(self):
        self.mgr.put("check.txt", b"x")
        assert self.mgr.exists("check.txt") is True

    def test_missing_via_manager(self):
        assert self.mgr.missing("no_file.txt") is True

    def test_url_via_manager(self):
        url = self.mgr.url("img.jpg")
        assert "img.jpg" in url

    def test_delete_via_manager(self):
        self.mgr.put("rm.txt", b"bye")
        self.mgr.delete("rm.txt")
        assert self.mgr.missing("rm.txt")

    def test_configure_clears_disk_cache(self):
        d1 = self.mgr.disk("local")
        self.mgr.configure(self.mgr._config)
        d2 = self.mgr.disk("local")
        assert d1 is not d2

    def test_s3_disk_resolved(self):
        from hunt.storage.s3 import S3Disk

        self.mgr._config["disks"]["s3"] = {
            "driver": "s3",
            "bucket": "mybucket",
            "key": "k",
            "secret": "s",
        }
        assert isinstance(self.mgr.disk("s3"), S3Disk)


# ---------------------------------------------------------------------------
# UploadedFile.store() integration
# ---------------------------------------------------------------------------


class TestUploadedFileStore:
    def setup_method(self):
        self._tmp = tempfile.mkdtemp()
        from hunt.storage.manager import Storage

        Storage.configure(
            {
                "default": "local",
                "disks": {
                    "local": {"driver": "local", "root": self._tmp},
                    "uploads": {"driver": "local", "root": self._tmp + "/uploads"},
                },
            }
        )

    def test_store_uses_storage_disk(self):
        from hunt.http.request import UploadedFile

        f = UploadedFile(filename="photo.jpg", content_type="image/jpeg", _data=b"imgbytes")
        stored_path = f.store("photos")
        expected = Path(self._tmp) / stored_path
        assert expected.exists()
        assert expected.read_bytes() == b"imgbytes"

    def test_store_returns_relative_path(self):
        from hunt.http.request import UploadedFile

        f = UploadedFile(filename="doc.pdf", content_type="application/pdf", _data=b"%PDF-1.4 fake")
        stored_path = f.store("docs")
        assert stored_path.startswith("docs/")
        assert stored_path.endswith(".pdf")

    def test_store_custom_disk(self):
        from hunt.http.request import UploadedFile

        f = UploadedFile(filename="a.txt", content_type="text/plain", _data=b"upload disk")
        stored_path = f.store("misc", disk="uploads")
        expected = Path(self._tmp) / "uploads" / stored_path
        assert expected.exists()

    def test_store_nested_directory(self):
        from hunt.http.request import UploadedFile

        f = UploadedFile(filename="img.png", content_type="image/png", _data=b"px")
        stored_path = f.store("img/2026/05")
        expected = Path(self._tmp) / stored_path
        assert expected.exists()


# ---------------------------------------------------------------------------
# Magic bytes MIME detection
# ---------------------------------------------------------------------------


class TestSniffMime:
    def _sniff(self, data):
        from hunt.http.request import _sniff_mime

        return _sniff_mime(data)

    def test_jpeg(self):
        assert self._sniff(b"\xff\xd8\xff\xe0" + b"\x00" * 100) == "image/jpeg"

    def test_png(self):
        assert self._sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) == "image/png"

    def test_gif(self):
        assert self._sniff(b"GIF89a" + b"\x00" * 100) == "image/gif"

    def test_webp(self):
        assert self._sniff(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100) == "image/webp"

    def test_svg(self):
        assert self._sniff(b"<svg xmlns='http://www.w3.org/2000/svg'>") == "image/svg+xml"

    def test_unknown_returns_octet_stream(self):
        assert self._sniff(b"\x00\x01\x02\x03arbitrary bytes") == "application/octet-stream"

    def test_get_mime_type_uses_magic_bytes_not_header(self):
        from hunt.http.request import UploadedFile

        # PNG bytes but lied about content-type in the header
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        f = UploadedFile(filename="evil.jpg", content_type="image/jpeg", _data=png_bytes)
        assert f.get_mime_type() == "image/png"

    def test_non_image_detected_despite_image_header(self):
        from hunt.http.request import UploadedFile

        # PHP-like script claiming to be JPEG
        f = UploadedFile(filename="shell.php", content_type="image/jpeg", _data=b"<?php echo 'bad'; ?>")
        assert f.get_mime_type() == "application/octet-stream"


# ---------------------------------------------------------------------------
# Static file serving — /storage/ path
# ---------------------------------------------------------------------------


class TestResolveStatic:
    @staticmethod
    def _roots(tmp_path):
        """Build the (kind, resolved_path) roots list for whichever dirs exist."""
        candidates = [("public", tmp_path / "public"), ("storage", tmp_path / "storage" / "app" / "public")]
        return [(kind, p.resolve()) for kind, p in candidates if p.is_dir()]

    def _resolve(self, path, tmp_path):
        from hunt.http.kernel import HttpKernel, _static_extensions

        return HttpKernel._resolve_static(path, self._roots(tmp_path), _static_extensions())

    def test_serves_from_public(self, tmp_path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "style.css").write_bytes(b"body{}")

        result = self._resolve("/style.css", tmp_path)
        assert result is not None
        assert result.read_bytes() == b"body{}"

    def test_serves_from_storage_app_public(self, tmp_path):
        storage_pub = tmp_path / "storage" / "app" / "public"
        storage_pub.mkdir(parents=True)
        (storage_pub / "photo.jpg").write_bytes(b"fakejpeg")

        result = self._resolve("/storage/photo.jpg", tmp_path)
        assert result is not None
        assert result.read_bytes() == b"fakejpeg"

    def test_disallowed_extension_not_served(self, tmp_path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "secret.env").write_bytes(b"SECRET=bad")

        # .env is not on the static allowlist → refused.
        assert self._resolve("/secret.env", tmp_path) is None

    def test_unknown_extension_refused_by_allowlist(self, tmp_path):
        public = tmp_path / "public"
        public.mkdir()
        (public / "archive.tar").write_bytes(b"data")

        # An extension absent from the allowlist is refused (denylist would miss this).
        assert self._resolve("/archive.tar", tmp_path) is None

    def test_nonexistent_returns_none(self, tmp_path):
        (tmp_path / "public").mkdir()
        assert self._resolve("/missing.png", tmp_path) is None

    def test_path_traversal_blocked(self, tmp_path):
        public = tmp_path / "public"
        public.mkdir()
        assert self._resolve("/../etc/passwd", tmp_path) is None


class TestStaticServingHeaders:
    def _kernel(self):
        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router

        return HttpKernel(Router())

    def test_caching_headers_and_conditional_304(self, tmp_path, monkeypatch):
        import asyncio

        public = tmp_path / "public"
        public.mkdir()
        (public / "app.css").write_bytes(b"body{}")
        monkeypatch.chdir(tmp_path)
        kernel = self._kernel()

        sent: list = []

        async def send(msg):
            sent.append(msg)

        # First request: full 200 with caching headers.
        ok = asyncio.run(kernel._try_static({"type": "http", "path": "/app.css", "headers": []}, send))
        assert ok
        start = sent[0]
        assert start["status"] == 200
        headers = dict(start["headers"])
        etag = headers[b"etag"]
        assert b"cache-control" in headers
        assert b"last-modified" in headers

        # Second request with matching If-None-Match: 304, empty body, no read.
        sent.clear()
        scope = {"type": "http", "path": "/app.css", "headers": [(b"if-none-match", etag)]}
        ok = asyncio.run(kernel._try_static(scope, send))
        assert ok
        assert sent[0]["status"] == 304
        assert sent[1]["body"] == b""

    def test_dynamic_path_skips_filesystem(self, tmp_path, monkeypatch):
        import asyncio

        (tmp_path / "public").mkdir()
        monkeypatch.chdir(tmp_path)
        kernel = self._kernel()

        async def send(msg):  # pragma: no cover - must not be called
            raise AssertionError("send should not be called for a dynamic route")

        # No extension → never treated as static, returns False without serving.
        assert asyncio.run(kernel._try_static({"type": "http", "path": "/users/42", "headers": []}, send)) is False


# ---------------------------------------------------------------------------
# storage:link command
# ---------------------------------------------------------------------------


class TestStorageLinkCommand:
    def test_creates_symlink(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.storage_link import storage_link_command

        (tmp_path / "public").mkdir()
        (tmp_path / "storage" / "app" / "public").mkdir(parents=True)

        runner = CliRunner()
        with runner.isolated_filesystem():
            import os

            os.chdir(tmp_path)
            result = runner.invoke(storage_link_command, catch_exceptions=False)

        assert result.exit_code == 0
        link = tmp_path / "public" / "storage"
        assert link.is_symlink()

    def test_existing_correct_link_noop(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.storage_link import storage_link_command

        (tmp_path / "public").mkdir()
        target = tmp_path / "storage" / "app" / "public"
        target.mkdir(parents=True)
        link = tmp_path / "public" / "storage"
        link.symlink_to(target)

        runner = CliRunner()
        with runner.isolated_filesystem():
            import os

            os.chdir(tmp_path)
            result = runner.invoke(storage_link_command, catch_exceptions=False)

        assert result.exit_code == 0
        assert "already exists" in result.output

    def test_existing_wrong_file_exits(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.storage_link import storage_link_command

        (tmp_path / "public").mkdir()
        (tmp_path / "storage" / "app" / "public").mkdir(parents=True)
        (tmp_path / "public" / "storage").mkdir()  # a real dir, not a symlink

        runner = CliRunner()
        with runner.isolated_filesystem():
            import os

            os.chdir(tmp_path)
            result = runner.invoke(storage_link_command)

        assert result.exit_code != 0
