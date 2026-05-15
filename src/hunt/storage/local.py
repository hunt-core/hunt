from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.http.request import UploadedFile

# Derive upload filename extensions from magic-byte-detected MIME type only.
# Client-supplied filenames are never trusted for extension.
_MIME_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
}


def _safe_extension(file: object) -> str:  # type: ignore[name-defined]
    try:
        mime = file.get_mime_type()  # type: ignore[attr-defined]
        return _MIME_EXT.get(mime, "")
    except Exception:
        return ""


class LocalDisk:
    """Filesystem disk backed by a local directory.

    All paths are relative to ``root``.  The public URL prefix is used by
    ``url()`` to build absolute URLs for publicly-accessible files.
    """

    def __init__(self, root: str | Path, url_prefix: str = "") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._url_prefix = url_prefix.rstrip("/")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def path(self, relative: str = "") -> str:
        """Return the absolute filesystem path for a relative storage path."""
        return str(self._root / relative) if relative else str(self._root)

    def put(self, path: str, contents: bytes | str) -> bool:
        """Write contents to path. Returns True on success."""
        dest = self._resolve(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, str):
            dest.write_text(contents, encoding="utf-8")
        else:
            dest.write_bytes(contents)
        return True

    def put_file(self, directory: str, file: UploadedFile, name: str | None = None) -> str:
        """Store an UploadedFile and return the stored relative path.

        When no explicit name is given a UUID-based name is generated so
        concurrent uploads of identically-named files never overwrite each other.
        """
        if name is not None:
            filename = Path(name).name
        else:
            import uuid

            filename = f"{uuid.uuid4().hex}{_safe_extension(file)}"
        stored_path = f"{directory.rstrip('/')}/{filename}"
        self.put(stored_path, file.content)
        return stored_path

    def get(self, path: str) -> bytes:
        """Read and return raw bytes from path."""
        return self._resolve(path).read_bytes()

    def get_text(self, path: str, encoding: str = "utf-8") -> str:
        """Read and return text from path."""
        return self._resolve(path).read_text(encoding=encoding)

    def exists(self, path: str) -> bool:
        """Return True if the file or directory exists."""
        return self._resolve(path).exists()

    def missing(self, path: str) -> bool:
        return not self.exists(path)

    def delete(self, path: str | list[str]) -> bool:
        """Delete one or more files. Returns True if all succeeded."""
        paths = [path] if isinstance(path, str) else path
        success = True
        for p in paths:
            target = self._resolve(p)
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                success = False
        return success

    def copy(self, from_path: str, to_path: str) -> bool:
        """Copy a file within the disk."""
        src = self._resolve(from_path)
        dst = self._resolve(to_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True

    def move(self, from_path: str, to_path: str) -> bool:
        """Move / rename a file within the disk."""
        src = self._resolve(from_path)
        dst = self._resolve(to_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return True

    # ------------------------------------------------------------------
    # Directory listing
    # ------------------------------------------------------------------

    def files(self, directory: str = "") -> list[str]:
        """Return a list of file paths (relative) in directory (non-recursive)."""
        target = self._resolve(directory) if directory else self._root
        if not target.is_dir():
            return []
        return [str(f.relative_to(self._root)) for f in target.iterdir() if f.is_file()]

    def all_files(self, directory: str = "") -> list[str]:
        """Return all files recursively."""
        target = self._resolve(directory) if directory else self._root
        if not target.is_dir():
            return []
        return [str(f.relative_to(self._root)) for f in target.rglob("*") if f.is_file()]

    def directories(self, directory: str = "") -> list[str]:
        """Return immediate sub-directories."""
        target = self._resolve(directory) if directory else self._root
        if not target.is_dir():
            return []
        return [str(d.relative_to(self._root)) for d in target.iterdir() if d.is_dir()]

    def make_directory(self, path: str) -> bool:
        self._resolve(path).mkdir(parents=True, exist_ok=True)
        return True

    def delete_directory(self, directory: str) -> bool:
        target = self._resolve(directory)
        if target.is_dir():
            shutil.rmtree(target)
        return True

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def size(self, path: str) -> int:
        """Return file size in bytes."""
        return self._resolve(path).stat().st_size

    def last_modified(self, path: str) -> int:
        """Return file modification time as Unix timestamp."""
        return int(self._resolve(path).stat().st_mtime)

    def mime_type(self, path: str) -> str:
        """Return a best-guess MIME type for the file."""
        import mimetypes

        mt, _ = mimetypes.guess_type(str(self._resolve(path)))
        return mt or "application/octet-stream"

    # ------------------------------------------------------------------
    # URL
    # ------------------------------------------------------------------

    def url(self, path: str) -> str:
        """Return the public URL for the given path."""
        clean = path.lstrip("/")
        if self._url_prefix:
            return f"{self._url_prefix}/{clean}"
        return f"/storage/{clean}"

    # ------------------------------------------------------------------
    # Append / prepend
    # ------------------------------------------------------------------

    def append(self, path: str, contents: str | bytes) -> bool:
        dest = self._resolve(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        mode = "ab" if isinstance(contents, bytes) else "a"
        enc = None if isinstance(contents, bytes) else "utf-8"
        with open(dest, mode, encoding=enc) as f:
            f.write(contents)
        return True

    def prepend(self, path: str, contents: str | bytes) -> bool:
        existing = self.get(path) if self.exists(path) else b""
        if isinstance(contents, str):
            contents = contents.encode()
        if isinstance(existing, str):
            existing = existing.encode()
        return self.put(path, contents + existing)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(self, relative: str) -> Path:
        resolved = (self._root / relative).resolve()
        root_resolved = self._root.resolve()
        if not str(resolved).startswith(str(root_resolved) + os.sep) and resolved != root_resolved:
            raise ValueError(f"Path traversal attempt: {relative!r} escapes storage root")
        return resolved
