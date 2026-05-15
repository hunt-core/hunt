from __future__ import annotations

from typing import Any

from hunt.storage.local import LocalDisk
from hunt.storage.s3 import S3Disk


class _StorageManager:
    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._disks: dict[str, Any] = {}

    def configure(self, config: dict[str, Any]) -> None:
        self._config = config
        self._disks.clear()

    def disk(self, name: str | None = None) -> LocalDisk | S3Disk:
        key = name or self._config.get("default", "local")
        if key not in self._disks:
            self._disks[key] = self._resolve(key)
        return self._disks[key]

    def _resolve(self, name: str) -> LocalDisk | S3Disk:
        disks = self._config.get("disks", {})
        cfg = disks.get(name)
        if cfg is None:
            raise RuntimeError(f"Storage disk '{name}' is not configured.")
        driver = cfg.get("driver", "local")
        if driver == "local":
            return LocalDisk(root=cfg["root"], url_prefix=cfg.get("url", ""))
        if driver == "s3":
            return S3Disk(config=cfg)
        raise RuntimeError(f"Unsupported storage driver: '{driver}'")

    # Convenience proxies — delegate to the default disk
    def put(self, path: str, contents: bytes | str) -> bool:
        return self.disk().put(path, contents)

    def get(self, path: str) -> bytes:
        return self.disk().get(path)

    def get_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.disk().get_text(path, encoding)

    def exists(self, path: str) -> bool:
        return self.disk().exists(path)

    def missing(self, path: str) -> bool:
        return self.disk().missing(path)

    def delete(self, path: str | list[str]) -> bool:
        return self.disk().delete(path)

    def url(self, path: str) -> str:
        return self.disk().url(path)


Storage = _StorageManager()
