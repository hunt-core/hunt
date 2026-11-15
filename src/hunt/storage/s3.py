from __future__ import annotations

import datetime as _dt
import mimetypes
from typing import Any
from urllib.parse import quote

from hunt.storage._naming import upload_filename

try:
    from hunt.log.manager import Log
except Exception:  # pragma: no cover
    Log = None  # type: ignore[assignment]

# S3 DeleteObjects accepts at most this many keys per request.
_DELETE_BATCH = 1000


def _is_not_found(exc: Exception) -> bool:
    """True if *exc* is a botocore ``ClientError`` meaning "no such object".

    Checked by shape (``exc.response``) so botocore does not need importing.
    Throttling, auth and network errors are not "not found" and must propagate.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str((response.get("Error") or {}).get("Code", ""))
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NotFound") or status == 404


class S3Disk:
    """S3-compatible disk via boto3 (optional dependency).

    Install boto3 to use: ``pip install boto3``

    Config keys: bucket, region, key, secret, url (CDN prefix), endpoint (for MinIO etc.)

    A ready-made client can be injected with ``S3Disk(config, client=...)``.
    """

    def __init__(self, config: dict, client: Any = None) -> None:
        self._bucket = config["bucket"]
        self._url_prefix = (config.get("url") or "").rstrip("/")
        self._endpoint = config.get("endpoint")
        self._client: Any = client
        self._config = config

    def _boto(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("boto3 is required for S3 storage. Install it: pip install boto3") from exc
            kwargs: dict[str, Any] = {
                "aws_access_key_id": self._config.get("key"),
                "aws_secret_access_key": self._config.get("secret"),
                "region_name": self._config.get("region", "us-east-1"),
            }
            if self._endpoint:
                kwargs["endpoint_url"] = self._endpoint
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def put(self, path: str, contents: bytes | str, content_type: str | None = None) -> bool:
        """Upload *contents*. The Content-Type is guessed from the key unless given."""
        if isinstance(contents, str):
            contents = contents.encode()
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": path, "Body": contents}
        content_type = content_type or mimetypes.guess_type(path)[0]
        if content_type:
            kwargs["ContentType"] = content_type
        self._boto().put_object(**kwargs)
        return True

    def put_file(self, directory: str, file: Any, name: str | None = None) -> str:
        filename = upload_filename(file, name)
        stored_path = f"{directory.rstrip('/')}/{filename}"
        self.put(stored_path, file.content)
        return stored_path

    def _get_or_empty(self, path: str) -> bytes:
        """Return the object's bytes, or ``b""`` only if it definitely does not exist."""
        try:
            return self.get(path)
        except Exception as exc:
            if _is_not_found(exc):
                return b""
            raise

    def append(self, path: str, contents: str | bytes) -> bool:
        """Append via read-modify-write (S3 has no native append; not atomic).

        A failed read (throttling, network) raises instead of being treated as
        an empty object, so existing data is never overwritten by mistake.
        """
        existing = self._get_or_empty(path)
        if isinstance(contents, str):
            contents = contents.encode()
        return self.put(path, existing + contents)

    def prepend(self, path: str, contents: str | bytes) -> bool:
        """Prepend via read-modify-write (S3 has no native prepend; not atomic)."""
        existing = self._get_or_empty(path)
        if isinstance(contents, str):
            contents = contents.encode()
        return self.put(path, contents + existing)

    def get(self, path: str) -> bytes:
        response = self._boto().get_object(Bucket=self._bucket, Key=path)
        return response["Body"].read()

    def get_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.get(path).decode(encoding)

    def exists(self, path: str) -> bool:
        """Return whether the object exists.

        Only a 404 / NoSuchKey answer means "absent"; any other failure
        (throttling, credentials, network) is re-raised.
        """
        try:
            self._boto().head_object(Bucket=self._bucket, Key=path)
            return True
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise

    def missing(self, path: str) -> bool:
        return not self.exists(path)

    def delete(self, path: str | list[str]) -> bool:
        """Delete one or more objects, 1000 per request.

        Returns False (after attempting every batch) if S3 reported an error
        for any key.
        """
        paths = [path] if isinstance(path, str) else list(path)
        success = True
        for start in range(0, len(paths), _DELETE_BATCH):
            batch = paths[start : start + _DELETE_BATCH]
            response = self._boto().delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": p} for p in batch]},
            )
            errors = response.get("Errors") if isinstance(response, dict) else None
            if isinstance(errors, list) and errors:
                success = False
                if Log is not None:
                    first = errors[0]
                    Log.error(
                        "S3 delete failed",
                        bucket=self._bucket,
                        failed=len(errors),
                        key=first.get("Key"),
                        code=first.get("Code"),
                    )
        return success

    def copy(self, from_path: str, to_path: str) -> bool:
        self._boto().copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": from_path},
            Key=to_path,
        )
        return True

    def move(self, from_path: str, to_path: str) -> bool:
        self.copy(from_path, to_path)
        return self.delete(from_path)

    def _pages(self, prefix: str, delimiter: str | None = None) -> Any:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
        if delimiter:
            kwargs["Delimiter"] = delimiter
        return self._boto().get_paginator("list_objects_v2").paginate(**kwargs)

    @staticmethod
    def _prefix(directory: str) -> str:
        return directory.rstrip("/") + "/" if directory else ""

    def files(self, directory: str = "") -> list[str]:
        pages = self._pages(self._prefix(directory), "/")
        return [obj["Key"] for page in pages for obj in page.get("Contents", []) if not obj["Key"].endswith("/")]

    def all_files(self, directory: str = "") -> list[str]:
        pages = self._pages(self._prefix(directory))
        return [obj["Key"] for page in pages for obj in page.get("Contents", []) if not obj["Key"].endswith("/")]

    def directories(self, directory: str = "") -> list[str]:
        """Return immediate sub-directories (S3 common prefixes), no trailing slash."""
        pages = self._pages(self._prefix(directory), "/")
        return [cp["Prefix"].rstrip("/") for page in pages for cp in page.get("CommonPrefixes", [])]

    def size(self, path: str) -> int:
        response = self._boto().head_object(Bucket=self._bucket, Key=path)
        return response["ContentLength"]

    def mime_type(self, path: str) -> str:
        """Best-guess MIME type from the key's extension (matches LocalDisk)."""
        mt, _ = mimetypes.guess_type(path)
        return mt or "application/octet-stream"

    def last_modified(self, path: str) -> int:
        response = self._boto().head_object(Bucket=self._bucket, Key=path)
        return int(response["LastModified"].timestamp())

    def url(self, path: str) -> str:
        """Public URL for the object; the key is percent-encoded.

        Uses the ``url`` prefix when configured, otherwise the custom
        ``endpoint`` (path-style, as MinIO and most S3-compatible servers
        expect), otherwise the AWS virtual-hosted URL.
        """
        key = quote(path.lstrip("/"), safe="/-_.~")
        if self._url_prefix:
            return f"{self._url_prefix}/{key}"
        if self._endpoint:
            return f"{str(self._endpoint).rstrip('/')}/{self._bucket}/{key}"
        region = self._config.get("region", "us-east-1")
        return f"https://{self._bucket}.s3.{region}.amazonaws.com/{key}"

    def temporary_url(self, path: str, expires: int | _dt.timedelta = 3600, method: str = "get_object") -> str:
        """Return a pre-signed URL valid for *expires* seconds (or a timedelta)."""
        seconds = int(expires.total_seconds()) if isinstance(expires, _dt.timedelta) else int(expires)
        if seconds <= 0:
            raise ValueError("temporary_url() needs a positive expiry.")
        return self._boto().generate_presigned_url(
            method,
            Params={"Bucket": self._bucket, "Key": path},
            ExpiresIn=seconds,
        )

    def path(self, relative: str = "") -> str:
        return f"s3://{self._bucket}/{relative}"

    def make_directory(self, path: str) -> bool:
        self.put(path.rstrip("/") + "/", b"")
        return True

    def delete_directory(self, directory: str) -> bool:
        files = self.all_files(directory)
        if files:
            return self.delete(files)
        return True
