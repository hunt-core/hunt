from __future__ import annotations

import logging
import posixpath
from typing import Any

from hunt.http.response import JsonResponse, Response

_log = logging.getLogger(__name__)

# Raster images only. SVG is deliberately absent: it can carry inline script
# and would run in the site's origin when opened from the public disk.
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_PER_PAGE = 60
_MAX_PER_PAGE = 200


def _disk():
    from hunt.admin.application import Admin
    from hunt.storage.manager import Storage

    return Storage.disk(Admin.media_disk)


def _path() -> str:
    from hunt.admin.application import Admin

    return Admin.media_path


def _flash(request: Any, key: str, msg: str) -> None:
    store = getattr(request, "_session", None)
    if store is not None:
        store.flash(key, msg)


def _max_bytes() -> int:
    from hunt.admin.application import Admin

    try:
        return max(0, int(Admin.media_max_kb)) * 1024
    except (TypeError, ValueError):
        return 10240 * 1024


def _int_param(request: Any, name: str, default: int) -> int:
    raw = request.query(name, None)
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def index(request: Any) -> Response:
    from hunt.admin.application import Admin

    Admin.require_tool(request, "media")
    ctx = Admin._base_context(request)
    ctx["title"] = "Media Manager"
    return Admin._render("admin/media/index.html", ctx)


def api_list(request: Any) -> Response:
    from hunt.admin.application import Admin

    Admin.require_tool(request, "media")
    disk = _disk()
    media_path = _path()
    try:
        paths = sorted(disk.files(media_path), reverse=True)
    except Exception:
        _log.exception("Media manager: could not list %r", media_path)
        paths = []

    total = len(paths)
    per_page = min(_MAX_PER_PAGE, max(1, _int_param(request, "per_page", _PER_PAGE)))
    last_page = max(1, (total + per_page - 1) // per_page)
    page = min(last_page, max(1, _int_param(request, "page", 1)))
    offset = (page - 1) * per_page

    files_out: list[dict] = []
    # url() and size() are only called for the page being returned.
    for p in paths[offset : offset + per_page]:
        try:
            name = p.rsplit("/", 1)[-1]
            url = disk.url(p)
            size = disk.size(p)
            files_out.append({"name": name, "path": p, "url": url, "size": size})
        except Exception:
            continue
    return JsonResponse(
        {"files": files_out, "total": total, "page": page, "per_page": per_page, "last_page": last_page}
    )


def upload(request: Any) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_tool
    from hunt.http.response import HttpException

    Admin.require_tool(request, "media")
    file = request.file("file")
    if file is None:
        raise HttpException(422, "No file provided.")
    mime = file.get_mime_type()
    if mime not in _ALLOWED_MIME:
        raise HttpException(422, f"File type not allowed: {mime}")
    limit = _max_bytes()
    size = file.size
    if limit and isinstance(size, int) and size > limit:
        raise HttpException(422, f"The file may not be greater than {limit // 1024} KB.")
    disk = _disk()
    stored_path = disk.put_file(_path(), file)
    url = disk.url(stored_path)
    name = stored_path.rsplit("/", 1)[-1]
    audit_tool("media", "upload", stored_path)
    return JsonResponse({"path": stored_path, "url": url, "name": name}, status=201)


def _safe_media_path(raw: str, media_path: str) -> str | None:
    """Normalise ``raw`` and return it only if it stays inside ``media_path``."""
    if not raw or "\x00" in raw or "\\" in raw:
        return None
    if raw.startswith("/") or ".." in raw.split("/"):
        return None
    normalised = posixpath.normpath(raw)
    if normalised in ("", ".") or normalised.startswith(("/", "../")) or normalised == "..":
        return None
    root = posixpath.normpath(media_path.strip("/")) if media_path.strip("/") else ""
    if not root or root in (".", ".."):
        return None
    if not normalised.startswith(root + "/"):
        return None
    return normalised


def delete(request: Any) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_tool
    from hunt.http.response import HttpException

    Admin.require_tool(request, "media")
    path = str(request.input("path") or "").strip()
    if not path:
        raise HttpException(422, "Path is required.")
    # A plain prefix test is not enough: "media/../uploads/x.jpg" starts with
    # "media/" yet names a file outside it.
    safe_path = _safe_media_path(path, _path())
    if safe_path is None:
        raise HttpException(403, "Forbidden.")
    disk = _disk()
    disk.delete(safe_path)
    audit_tool("media", "delete", safe_path)
    return JsonResponse({"deleted": True})
