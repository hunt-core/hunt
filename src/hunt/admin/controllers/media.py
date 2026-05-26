from __future__ import annotations

from typing import Any

from hunt.http.response import JsonResponse, Response

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}


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


def index(request: Any) -> Response:
    from hunt.admin.application import Admin

    ctx = Admin._base_context(request)
    ctx["title"] = "Media Manager"
    return Admin._render("admin/media/index.html", ctx)


def api_list(request: Any) -> Response:
    disk = _disk()
    media_path = _path()
    files_out: list[dict] = []
    try:
        paths = disk.files(media_path)
    except Exception:
        paths = []
    for p in sorted(paths, reverse=True):
        try:
            name = p.rsplit("/", 1)[-1]
            url = disk.url(p)
            size = disk.size(p)
            files_out.append({"name": name, "path": p, "url": url, "size": size})
        except Exception:
            continue
    return JsonResponse({"files": files_out})


def upload(request: Any) -> Response:
    from hunt.http.response import HttpException

    file = request.file("file")
    if file is None:
        raise HttpException(422, "No file provided.")
    mime = file.get_mime_type()
    if mime not in _ALLOWED_MIME:
        raise HttpException(422, f"File type not allowed: {mime}")
    disk = _disk()
    stored_path = disk.put_file(_path(), file)
    url = disk.url(stored_path)
    name = stored_path.rsplit("/", 1)[-1]
    return JsonResponse({"path": stored_path, "url": url, "name": name}, status=201)


def delete(request: Any) -> Response:
    from hunt.http.response import HttpException

    path = (request.input("path") or "").strip()
    if not path:
        raise HttpException(422, "Path is required.")
    media_path = _path().rstrip("/")
    if not path.startswith(media_path + "/"):
        raise HttpException(403, "Forbidden.")
    disk = _disk()
    disk.delete(path)
    return JsonResponse({"deleted": True})
