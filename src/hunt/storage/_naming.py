"""Shared upload-naming logic for storage disks.

Both ``LocalDisk`` and ``S3Disk`` derive stored filenames through
``upload_filename`` so their ``put_file`` behaviour is identical regardless of
backend: an explicit name is reduced to a basename, and an auto-generated name
is a UUID plus a safe, content-verified extension.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

# Fallback extension. Never empty: an extensionless upload is served with the
# wrong content type and is harder to reason about than an explicitly opaque one.
_DEFAULT_EXT = ".bin"

# Extension derived from the magic-byte-detected MIME type.
_MIME_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
}

# Client-supplied extensions that may be honoured, each mapped to the sniffed
# MIME types the content must actually match.  The allowlist — not the sniff —
# is the security boundary: it is what keeps .php/.html/.js off disk.
#
# ZIP-container formats are why this exists at all.  .xlsx and .docx are byte
# identical at the magic-number level, so sniffing alone cannot name them and
# they end up extensionless; the filename is the only disambiguator.
# Formats the sniffer has no branch for (text, mp4, mp3) also accept
# octet-stream, which is what they currently detect as.
_ALLOWED_EXT: dict[str, frozenset[str]] = {
    ".jpg": frozenset({"image/jpeg"}),
    ".jpeg": frozenset({"image/jpeg"}),
    ".png": frozenset({"image/png"}),
    ".gif": frozenset({"image/gif"}),
    ".webp": frozenset({"image/webp"}),
    ".bmp": frozenset({"image/bmp"}),
    ".ico": frozenset({"image/x-icon"}),
    ".pdf": frozenset({"application/pdf"}),
    ".zip": frozenset({"application/zip"}),
    ".xlsx": frozenset({"application/zip"}),
    ".docx": frozenset({"application/zip"}),
    ".pptx": frozenset({"application/zip"}),
    ".odt": frozenset({"application/zip"}),
    ".ods": frozenset({"application/zip"}),
    ".txt": frozenset({"text/plain", "application/octet-stream"}),
    ".csv": frozenset({"text/plain", "application/octet-stream"}),
    ".mp4": frozenset({"video/mp4", "application/octet-stream"}),
    ".mp3": frozenset({"audio/mpeg", "application/octet-stream"}),
}


def safe_extension(file: Any) -> str:
    """Pick a storage extension for an upload.

    Honours the client-supplied extension only when it is allowlisted *and*
    consistent with the magic-byte sniff; otherwise falls back to the sniffed
    type, and finally to ``_DEFAULT_EXT``.
    """
    try:
        mime = file.get_mime_type()
    except Exception:
        return _DEFAULT_EXT
    try:
        ext = Path(getattr(file, "filename", "") or "").suffix.lower()
    except Exception:
        ext = ""
    if ext and mime in _ALLOWED_EXT.get(ext, frozenset()):
        return ext
    return _MIME_EXT.get(mime, _DEFAULT_EXT)


def upload_filename(file: Any, name: str | None = None) -> str:
    """Return the stored filename for an upload.

    With an explicit *name* the basename is used (path components stripped so a
    caller-supplied name can never escape the target directory).  Otherwise a
    UUID name is generated so concurrent uploads of identically-named files
    never overwrite each other, with a safe extension from ``safe_extension``.
    """
    if name is not None:
        return Path(name).name
    return f"{uuid.uuid4().hex}{safe_extension(file)}"
