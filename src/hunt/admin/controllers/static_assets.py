from __future__ import annotations

import mimetypes
from pathlib import Path

from hunt.http.response import HttpException, Response

# Bundled assets shipped with the package.
_PACKAGE_STATIC = Path(__file__).parent.parent / "static"

# User overrides: publish assets to public/vendor/hunt-admin/ in the project root
# to replace any bundled file without touching the package.
_USER_STATIC = Path.cwd() / "public" / "vendor" / "hunt-admin"

# Explicit allow-list of extensions that may be served.
_ALLOWED_SUFFIXES = {".css", ".js", ".eot", ".woff", ".woff2", ".ttf", ".svg", ".png"}


def _resolve(filename: str, base: Path) -> Path | None:
    """Resolve filename within base, returning None if it escapes the directory."""
    try:
        target = (base / filename).resolve()
        target.relative_to(base.resolve())
        return target
    except (ValueError, OSError):
        return None


def serve(request: object, filename: str) -> Response:
    """Serve a static admin asset.

    User overrides in ``public/vendor/hunt-admin/`` take precedence over the
    bundled package assets so individual files can be replaced without
    re-running the build.
    """
    # Try user override first, then fall back to bundled package asset.
    target: Path | None = None
    for base in (_USER_STATIC, _PACKAGE_STATIC):
        candidate = _resolve(filename, base)
        if candidate is not None and candidate.suffix in _ALLOWED_SUFFIXES and candidate.is_file():
            target = candidate
            break

    if target is None:
        raise HttpException(404, "Not found.")

    content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return Response(target.read_bytes(), content_type=content_type)
