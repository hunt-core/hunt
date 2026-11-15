"""Regression tests for STOR-01, STOR-02 and STOR-L1."""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from hunt.storage._naming import upload_filename
from hunt.storage.local import LocalDisk
from hunt.storage.s3 import S3Disk


class ClientError(Exception):
    """Shaped like botocore.exceptions.ClientError (boto3 is not installed)."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _Paginator:
    def __init__(self, client):
        self.client = client

    def paginate(self, **kwargs):
        self.client.calls.append(("paginate", kwargs))
        prefix = kwargs.get("Prefix", "")
        delimiter = kwargs.get("Delimiter")
        keys = sorted(k for k in self.client.objects if k.startswith(prefix))
        contents, prefixes = [], []
        for key in keys:
            rest = key[len(prefix) :]
            if delimiter and delimiter in rest:
                common = prefix + rest.split(delimiter)[0] + delimiter
                if common not in prefixes:
                    prefixes.append(common)
            else:
                contents.append(key)
        # Two entries per page so every listing spans several pages.
        entries = [("c", k) for k in contents] + [("p", p) for p in prefixes]
        for start in range(0, max(len(entries), 1), 2):
            page: dict = {}
            for kind, value in entries[start : start + 2]:
                if kind == "c":
                    page.setdefault("Contents", []).append({"Key": value})
                else:
                    page.setdefault("CommonPrefixes", []).append({"Prefix": value})
            yield page


class StubS3:
    """In-memory stub of the boto3 S3 client methods S3Disk uses."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, Exception] = {}
        self.delete_errors: set[str] = set()

    def _call(self, name, kwargs):
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise self.fail[name]

    def named(self, name):
        return [kwargs for n, kwargs in self.calls if n == name]

    def put_object(self, **kwargs):
        self._call("put_object", kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def get_object(self, **kwargs):
        self._call("get_object", kwargs)
        if kwargs["Key"] not in self.objects:
            raise ClientError("NoSuchKey", 404)
        return {"Body": _Body(self.objects[kwargs["Key"]])}

    def head_object(self, **kwargs):
        self._call("head_object", kwargs)
        if kwargs["Key"] not in self.objects:
            raise ClientError("404", 404)
        return {"ContentLength": len(self.objects[kwargs["Key"]])}

    def delete_objects(self, **kwargs):
        self._call("delete_objects", kwargs)
        keys = [o["Key"] for o in kwargs["Delete"]["Objects"]]
        assert len(keys) <= 1000, "S3 rejects more than 1000 keys per DeleteObjects call"
        errors = []
        for key in keys:
            if key in self.delete_errors:
                errors.append({"Key": key, "Code": "AccessDenied", "Message": "Access Denied"})
            else:
                self.objects.pop(key, None)
        return {"Errors": errors} if errors else {"Deleted": [{"Key": k} for k in keys]}

    def copy_object(self, **kwargs):
        self._call("copy_object", kwargs)
        self.objects[kwargs["Key"]] = self.objects[kwargs["CopySource"]["Key"]]

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Paginator(self)

    def list_objects_v2(self, **kwargs):  # pragma: no cover - must not be used
        raise AssertionError("unpaginated list_objects_v2 call")

    def generate_presigned_url(self, method, Params, ExpiresIn):
        self.calls.append(("generate_presigned_url", {"method": method, "Params": Params, "ExpiresIn": ExpiresIn}))
        return f"https://signed.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


@pytest.fixture
def client():
    return StubS3()


@pytest.fixture
def disk(client):
    return S3Disk({"bucket": "bkt", "region": "eu-west-1", "key": "k", "secret": "s"}, client=client)


# ---------------------------------------------------------------------------
# STOR-01: S3
# ---------------------------------------------------------------------------


class TestS3Exists:
    def test_client_can_be_injected(self, disk, client):
        assert disk._boto() is client

    def test_exists_true_and_false(self, disk, client):
        client.objects["a.txt"] = b"x"
        assert disk.exists("a.txt") is True
        assert disk.exists("nope.txt") is False
        assert disk.missing("nope.txt") is True

    @pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
    def test_not_found_codes_mean_absent(self, disk, client, code):
        client.fail["head_object"] = ClientError(code, 404)
        assert disk.exists("x") is False

    @pytest.mark.parametrize(
        "error",
        [
            ClientError("SlowDown", 503),
            ClientError("AccessDenied", 403),
            ClientError("InternalError", 500),
            ConnectionError("network unreachable"),
            TimeoutError("timed out"),
        ],
    )
    def test_other_errors_are_reraised(self, disk, client, error):
        client.objects["a.txt"] = b"x"
        client.fail["head_object"] = error
        with pytest.raises(type(error)):
            disk.exists("a.txt")
        with pytest.raises(type(error)):
            disk.missing("a.txt")


class TestS3AppendPrepend:
    def test_append_and_prepend(self, disk, client):
        client.objects["log.txt"] = b"abc"
        disk.append("log.txt", "def")
        assert client.objects["log.txt"] == b"abcdef"
        disk.prepend("log.txt", b"00")
        assert client.objects["log.txt"] == b"00abcdef"

    def test_append_to_missing_object_creates_it(self, disk, client):
        disk.append("new.txt", "data")
        assert client.objects["new.txt"] == b"data"
        disk.prepend("new2.txt", "data")
        assert client.objects["new2.txt"] == b"data"

    @pytest.mark.parametrize("method", ["append", "prepend"])
    @pytest.mark.parametrize("failing_call", ["get_object", "head_object"])
    def test_transient_error_never_overwrites_existing_data(self, disk, client, method, failing_call):
        client.objects["log.txt"] = b"precious"
        client.fail[failing_call] = ClientError("SlowDown", 503)
        if failing_call == "get_object":
            with pytest.raises(ClientError):
                getattr(disk, method)("log.txt", "more")
        else:
            # A failing HEAD must not be read as "object is empty".
            getattr(disk, method)("log.txt", "more")
            assert b"precious" in client.objects["log.txt"]
        assert client.objects["log.txt"].find(b"precious") >= 0
        if failing_call == "get_object":
            assert client.named("put_object") == []


class TestS3Delete:
    def test_delete_is_chunked_by_1000(self, disk, client):
        keys = [f"f/{i}.txt" for i in range(2500)]
        client.objects.update(dict.fromkeys(keys, b""))
        assert disk.delete(keys) is True
        sizes = [len(c["Delete"]["Objects"]) for c in client.named("delete_objects")]
        assert sizes == [1000, 1000, 500]
        assert client.objects == {}

    def test_delete_single_and_empty(self, disk, client):
        client.objects["a"] = b""
        assert disk.delete("a") is True
        assert disk.delete([]) is True
        assert len(client.named("delete_objects")) == 1

    def test_delete_reports_per_key_errors(self, disk, client):
        keys = [f"f/{i}.txt" for i in range(1500)]
        client.objects.update(dict.fromkeys(keys, b""))
        client.delete_errors = {"f/3.txt"}
        assert disk.delete(keys) is False
        # The remaining batch is still attempted.
        assert len(client.named("delete_objects")) == 2
        assert list(client.objects) == ["f/3.txt"]

    def test_delete_request_failure_propagates(self, disk, client):
        client.fail["delete_objects"] = ClientError("SlowDown", 503)
        with pytest.raises(ClientError):
            disk.delete("a")

    def test_delete_directory_removes_everything_and_reports_errors(self, disk, client):
        keys = [f"dir/{i:04d}.txt" for i in range(1200)]
        client.objects.update(dict.fromkeys(keys, b""))
        client.objects["other/keep.txt"] = b""
        assert disk.delete_directory("dir") is True
        assert list(client.objects) == ["other/keep.txt"]
        client.objects["dir/x"] = b""
        client.delete_errors = {"dir/x"}
        assert disk.delete_directory("dir") is False

    def test_move_reports_failed_delete(self, disk, client):
        client.objects["src"] = b"data"
        client.delete_errors = {"src"}
        assert disk.move("src", "dst") is False
        assert client.objects["dst"] == b"data"
        client.delete_errors = set()
        assert disk.move("src", "dst2") is True
        assert "src" not in client.objects


class TestS3Listing:
    def _fill(self, client):
        for key in ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt", "x/1.txt", "x/2.txt", "y/1.txt", "z/deep/1.txt", "w/"]:
            client.objects[key] = b""

    def test_files_reads_every_page(self, disk, client):
        self._fill(client)
        assert disk.files() == ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]
        assert disk.files("x") == ["x/1.txt", "x/2.txt"]

    def test_directories_reads_every_page(self, disk, client):
        self._fill(client)
        assert disk.directories() == ["w", "x", "y", "z"]
        assert disk.directories("z") == ["z/deep"]

    def test_all_files(self, disk, client):
        self._fill(client)
        assert len(disk.all_files()) == 9
        assert disk.all_files("z") == ["z/deep/1.txt"]

    def test_listing_passes_bucket_prefix_and_delimiter(self, disk, client):
        disk.files("x/")
        assert client.named("paginate")[-1] == {"Bucket": "bkt", "Prefix": "x/", "Delimiter": "/"}
        disk.all_files("x")
        assert client.named("paginate")[-1] == {"Bucket": "bkt", "Prefix": "x/"}


class TestS3PutAndUrls:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("img/a.png", "image/png"),
            ("doc.pdf", "application/pdf"),
            ("page.html", "text/html"),
            ("data.json", "application/json"),
        ],
    )
    def test_put_sets_content_type(self, disk, client, key, expected):
        disk.put(key, b"x")
        assert client.named("put_object")[-1]["ContentType"] == expected

    def test_put_without_known_type_omits_content_type(self, disk, client):
        disk.put("blob", "text")
        assert client.named("put_object")[-1] == {"Bucket": "bkt", "Key": "blob", "Body": b"text"}

    def test_put_content_type_override(self, disk, client):
        disk.put("a.bin", b"x", content_type="application/x-custom")
        assert client.named("put_object")[-1]["ContentType"] == "application/x-custom"

    def test_put_file_sets_content_type_from_stored_name(self, disk, client):
        stored = disk.put_file("up", _Upload("a.png", "image/png"))
        assert stored.endswith(".png")
        assert client.named("put_object")[-1]["ContentType"] == "image/png"

    def test_url_default_aws_is_quoted(self, disk):
        assert disk.url("my docs/a#b?.txt") == "https://bkt.s3.eu-west-1.amazonaws.com/my%20docs/a%23b%3F.txt"
        assert disk.url("/img/ü.png") == "https://bkt.s3.eu-west-1.amazonaws.com/img/%C3%BC.png"

    def test_url_uses_endpoint(self, client):
        d = S3Disk({"bucket": "bkt", "endpoint": "http://minio:9000/"}, client=client)
        assert d.url("a b/c.txt") == "http://minio:9000/bkt/a%20b/c.txt"

    def test_url_prefix_wins_and_is_quoted(self, client):
        d = S3Disk({"bucket": "bkt", "endpoint": "http://minio:9000", "url": "https://cdn.example.com/"}, client=client)
        assert d.url("a b.txt") == "https://cdn.example.com/a%20b.txt"

    def test_temporary_url(self, disk, client):
        url = disk.temporary_url("private/report.pdf", 600)
        assert url == "https://signed.example/bkt/private/report.pdf?X-Amz-Expires=600"
        assert client.named("generate_presigned_url")[-1] == {
            "method": "get_object",
            "Params": {"Bucket": "bkt", "Key": "private/report.pdf"},
            "ExpiresIn": 600,
        }
        disk.temporary_url("x", datetime.timedelta(minutes=5))
        assert client.named("generate_presigned_url")[-1]["ExpiresIn"] == 300
        disk.temporary_url("x")
        assert client.named("generate_presigned_url")[-1]["ExpiresIn"] == 3600

    def test_temporary_url_needs_positive_expiry(self, disk):
        with pytest.raises(ValueError):
            disk.temporary_url("x", 0)

    def test_storage_manager_proxies_temporary_url(self, client):
        from hunt.storage.manager import _StorageManager

        mgr = _StorageManager()
        mgr.configure({"default": "s3", "disks": {"s3": {"driver": "s3", "bucket": "bkt"}}})
        mgr.disk()._client = client
        assert "X-Amz-Expires=60" in mgr.temporary_url("a.txt", 60)


# ---------------------------------------------------------------------------
# STOR-02: LocalDisk roots
# ---------------------------------------------------------------------------


def _populate(disk):
    disk.put("a.txt", "a")
    disk.put("sub/b.txt", "b")
    disk.put("sub/deep/c.txt", "c")


class TestLocalDiskRoot:
    def test_relative_root_listing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        disk = LocalDisk("storage/app")
        _populate(disk)
        assert disk.files() == ["a.txt"]
        assert disk.files("sub") == [os.path.join("sub", "b.txt")]
        assert sorted(disk.all_files()) == sorted(
            ["a.txt", os.path.join("sub", "b.txt"), os.path.join("sub", "deep", "c.txt")]
        )
        assert disk.directories() == ["sub"]
        assert disk.directories("sub") == [os.path.join("sub", "deep")]
        assert (tmp_path / "storage" / "app" / "a.txt").read_text() == "a"

    def test_relative_root_survives_chdir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        disk = LocalDisk("data")
        disk.put("a.txt", "a")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert disk.get_text("a.txt") == "a"
        assert disk.files() == ["a.txt"]
        assert Path(disk.path()).is_absolute()

    def test_symlinked_root_listing(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not available")
        disk = LocalDisk(link)
        _populate(disk)
        assert disk.files() == ["a.txt"]
        assert sorted(disk.all_files()) == sorted(
            ["a.txt", os.path.join("sub", "b.txt"), os.path.join("sub", "deep", "c.txt")]
        )
        assert disk.directories() == ["sub"]
        assert (real / "sub" / "b.txt").read_text() == "b"

    def test_traversal_is_still_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        disk = LocalDisk("root")
        (tmp_path / "root-secret").mkdir()
        (tmp_path / "root-secret" / "x").write_text("secret")
        for bad in ("../outside.txt", "../root-secret/x", "/etc/passwd", "a/../../b"):
            with pytest.raises(ValueError):
                disk.get(bad)

    def test_local_disk_has_no_temporary_urls(self, tmp_path):
        with pytest.raises(NotImplementedError):
            LocalDisk(tmp_path).temporary_url("a.txt")


# ---------------------------------------------------------------------------
# STOR-L1: upload names
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@dataclass
class _Upload:
    filename: str
    mime: str = "text/plain"
    content: bytes = b"data"

    def get_mime_type(self) -> str:
        return self.mime


class TestUploadFilename:
    @pytest.mark.parametrize("name", ["", ".", "..", "/", "a/..", "..\\..", "x\x00.png"])
    def test_invalid_names_raise(self, name):
        with pytest.raises(ValueError, match="Invalid upload filename"):
            upload_filename(_Upload("a.txt"), name)

    @pytest.mark.parametrize("name", ["", ".", ".."])
    def test_invalid_names_do_not_reach_the_disk(self, tmp_path, name):
        disk = LocalDisk(tmp_path / "root")
        with pytest.raises(ValueError):
            disk.put_file("uploads", _Upload("a.txt"), name)
        assert disk.all_files() == []

    @pytest.mark.parametrize(
        ("name", "mime", "expected"),
        [
            ("shell.php", "text/plain", "shell.txt"),
            ("shell.PHP", "application/octet-stream", "shell.bin"),
            ("page.html", "text/plain", "page.txt"),
            ("evil.svg", "application/octet-stream", "evil.bin"),
            ("script.js", "text/plain", "script.txt"),
            ("fake.png", "text/plain", "fake.txt"),
            ("photo.jpg", "image/png", "photo.png"),
            ("noext", "image/png", "noext.png"),
            (".htaccess", "text/plain", ".htaccess.txt"),
        ],
    )
    def test_explicit_name_extension_goes_through_the_allow_list(self, name, mime, expected):
        assert upload_filename(_Upload("whatever.bin", mime), name) == expected

    @pytest.mark.parametrize(
        ("name", "mime"),
        [
            ("avatar.png", "image/png"),
            ("report.pdf", "application/pdf"),
            ("book.xlsx", "application/zip"),
            ("notes.txt", "text/plain"),
            ("data.CSV", "text/plain"),
        ],
    )
    def test_allowed_matching_extension_is_kept(self, name, mime):
        stem, _, ext = name.rpartition(".")
        assert upload_filename(_Upload("x", mime), name) == f"{stem}.{ext.lower()}"

    def test_client_filename_cannot_bypass_the_allow_list(self, tmp_path):
        # The documented foot-gun: disk.put_file(dir, file, file.filename).
        f = _Upload("../../shell.php", "text/plain", b"<?php system($_GET['c']); ?>")
        disk = LocalDisk(tmp_path / "root")
        stored = disk.put_file("uploads", f, f.filename)
        assert stored == "uploads/shell.txt"
        assert disk.all_files() == [os.path.join("uploads", "shell.txt")]

    def test_path_components_are_stripped(self):
        assert upload_filename(_Upload("x", "image/png"), "a/b/avatar.png") == "avatar.png"
        assert upload_filename(_Upload("x", "image/png"), "C:\\Users\\me\\avatar.png") == "avatar.png"

    def test_generated_names_are_unchanged(self):
        name = upload_filename(_Upload("photo.png", "image/png"))
        assert name.endswith(".png") and len(name) == 36
        assert upload_filename(_Upload("x.php", "text/plain")).endswith(".txt")

    def test_real_uploaded_file_is_sniffed(self):
        from hunt.http.request import UploadedFile

        real_png = UploadedFile(filename="a.png", content_type="image/png", _data=PNG)
        assert upload_filename(real_png, "avatar.png") == "avatar.png"
        fake_png = UploadedFile(filename="a.png", content_type="image/png", _data=b"<?php ?>")
        assert not upload_filename(fake_png, "avatar.php").endswith(".php")
