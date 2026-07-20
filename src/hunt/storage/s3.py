from __future__ import annotations

from typing import Any

from hunt.storage._naming import upload_filename


class S3Disk:
    """S3-compatible disk via boto3 (optional dependency).

    Install boto3 to use: ``pip install boto3``

    Config keys: bucket, region, key, secret, url (CDN prefix), endpoint (for MinIO etc.)
    """

    def __init__(self, config: dict) -> None:
        self._bucket = config["bucket"]
        self._url_prefix = config.get("url", "").rstrip("/")
        self._endpoint = config.get("endpoint")
        self._client: Any = None
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

    def put(self, path: str, contents: bytes | str) -> bool:
        if isinstance(contents, str):
            contents = contents.encode()
        self._boto().put_object(Bucket=self._bucket, Key=path, Body=contents)
        return True

    def put_file(self, directory: str, file: Any, name: str | None = None) -> str:
        filename = upload_filename(file, name)
        stored_path = f"{directory.rstrip('/')}/{filename}"
        self.put(stored_path, file.content)
        return stored_path

    def append(self, path: str, contents: str | bytes) -> bool:
        """Append via read-modify-write (S3 has no native append)."""
        existing = self.get(path) if self.exists(path) else b""
        if isinstance(contents, str):
            contents = contents.encode()
        return self.put(path, existing + contents)

    def prepend(self, path: str, contents: str | bytes) -> bool:
        """Prepend via read-modify-write (S3 has no native prepend)."""
        existing = self.get(path) if self.exists(path) else b""
        if isinstance(contents, str):
            contents = contents.encode()
        return self.put(path, contents + existing)

    def get(self, path: str) -> bytes:
        response = self._boto().get_object(Bucket=self._bucket, Key=path)
        return response["Body"].read()

    def get_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.get(path).decode(encoding)

    def exists(self, path: str) -> bool:
        try:
            self._boto().head_object(Bucket=self._bucket, Key=path)
            return True
        except Exception:
            return False

    def missing(self, path: str) -> bool:
        return not self.exists(path)

    def delete(self, path: str | list[str]) -> bool:
        paths = [path] if isinstance(path, str) else path
        objects = [{"Key": p} for p in paths]
        self._boto().delete_objects(Bucket=self._bucket, Delete={"Objects": objects})
        return True

    def copy(self, from_path: str, to_path: str) -> bool:
        self._boto().copy_object(
            Bucket=self._bucket,
            CopySource={"Bucket": self._bucket, "Key": from_path},
            Key=to_path,
        )
        return True

    def move(self, from_path: str, to_path: str) -> bool:
        self.copy(from_path, to_path)
        self.delete(from_path)
        return True

    def files(self, directory: str = "") -> list[str]:
        prefix = directory.rstrip("/") + "/" if directory else ""
        response = self._boto().list_objects_v2(Bucket=self._bucket, Prefix=prefix, Delimiter="/")
        return [obj["Key"] for obj in response.get("Contents", []) if not obj["Key"].endswith("/")]

    def all_files(self, directory: str = "") -> list[str]:
        prefix = directory.rstrip("/") + "/" if directory else ""
        paginator = self._boto().get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self._bucket, Prefix=prefix)
        return [obj["Key"] for page in pages for obj in page.get("Contents", []) if not obj["Key"].endswith("/")]

    def directories(self, directory: str = "") -> list[str]:
        """Return immediate sub-directories (S3 common prefixes), no trailing slash."""
        prefix = directory.rstrip("/") + "/" if directory else ""
        response = self._boto().list_objects_v2(Bucket=self._bucket, Prefix=prefix, Delimiter="/")
        return [cp["Prefix"].rstrip("/") for cp in response.get("CommonPrefixes", [])]

    def size(self, path: str) -> int:
        response = self._boto().head_object(Bucket=self._bucket, Key=path)
        return response["ContentLength"]

    def mime_type(self, path: str) -> str:
        """Best-guess MIME type from the key's extension (matches LocalDisk)."""
        import mimetypes

        mt, _ = mimetypes.guess_type(path)
        return mt or "application/octet-stream"

    def last_modified(self, path: str) -> int:
        response = self._boto().head_object(Bucket=self._bucket, Key=path)
        return int(response["LastModified"].timestamp())

    def url(self, path: str) -> str:
        if self._url_prefix:
            return f"{self._url_prefix}/{path.lstrip('/')}"
        region = self._config.get("region", "us-east-1")
        return f"https://{self._bucket}.s3.{region}.amazonaws.com/{path}"

    def path(self, relative: str = "") -> str:
        return f"s3://{self._bucket}/{relative}"

    def make_directory(self, path: str) -> bool:
        self.put(path.rstrip("/") + "/", b"")
        return True

    def delete_directory(self, directory: str) -> bool:
        files = self.all_files(directory)
        if files:
            self.delete(files)
        return True
