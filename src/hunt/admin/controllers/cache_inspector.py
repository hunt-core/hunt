from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from hunt.http.request import Request
from hunt.http.response import RedirectResponse, Response

_log = logging.getLogger(__name__)

_PER_PAGE = 50
# Hard cap on how many keys one page view will pull from the store.
_MAX_ENTRIES = 1000
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _flash(request: Request, key: str, msg: str) -> None:
    store = getattr(request, "_session", None)
    if store is not None:
        store.flash(key, msg)


def _store():
    from hunt.cache.manager import Cache

    return Cache._get_store()


def _list_entries() -> tuple[str, list[dict]]:
    """Return (driver_name, list of entry dicts)."""
    from hunt.cache.manager import ArrayStore, FileStore, RedisStore

    store = _store()

    if isinstance(store, RedisStore):
        return "redis", _list_redis(store)
    if isinstance(store, FileStore):
        return "file", _list_file(store)
    if isinstance(store, ArrayStore):
        return "array", _list_array(store)
    return "unknown", []


def _list_redis(store: Any) -> list[dict]:
    entries = []
    prefix = store._prefix
    cursor = 0
    try:
        while True:
            cursor, keys = store._redis.scan(cursor, match=f"{prefix}*", count=200)
            for raw_key in keys:
                if len(entries) >= _MAX_ENTRIES:
                    break
                k = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                display_key = k[len(prefix) :]
                ttl = store._redis.ttl(k)
                raw_val = store._redis.get(k)
                try:
                    value = json.loads(raw_val) if raw_val is not None else None
                except Exception:
                    value = repr(raw_val)
                entries.append(
                    {
                        "key": display_key,
                        "ttl": ttl if ttl >= 0 else None,
                        "value": _truncate(value),
                        "forever": ttl == -1,
                    }
                )
            # Stop scanning once the cap is reached: the listing is bounded
            # however large the keyspace is.
            if cursor == 0 or len(entries) >= _MAX_ENTRIES:
                break
    except Exception:
        _log.exception("Cache inspector: could not list redis keys")
    return sorted(entries, key=lambda e: e["key"])


def _list_array(store: Any) -> list[dict]:
    entries = []
    now = time.time()
    for key, (value, expires_at) in list(store._data.items()):
        if expires_at and expires_at < now:
            continue
        ttl = int(expires_at - now) if expires_at else None
        entries.append(
            {
                "key": key,
                "ttl": ttl,
                "value": _truncate(value),
                "forever": expires_at == 0,
            }
        )
    return sorted(entries, key=lambda e: e["key"])


def _list_file(store: Any) -> list[dict]:
    """List file-store entries.

    The file store names each file after a hash of its key, so the original
    key is only known when the payload records it (``"key"``). Either way the
    entry carries ``file`` (the on-disk name), which is what Delete uses: the
    hash posted back as a key would never match anything.
    """
    entries = []
    now = time.time()
    try:
        for f in store._path.rglob("*"):
            if len(entries) >= _MAX_ENTRIES:
                break
            if not f.is_file() or f.name.endswith(".tmp"):
                continue
            try:
                payload = json.loads(f.read_text())
                ea = payload.get("expires_at", 0)
                if ea and ea < now:
                    continue
                ttl = int(ea - now) if ea else None
                original_key = payload.get("key")
                entries.append(
                    {
                        "key": original_key if isinstance(original_key, str) and original_key else f.name,
                        "file": f.name,
                        "ttl": ttl,
                        "value": _truncate(payload.get("value")),
                        "forever": ea == 0,
                    }
                )
            except Exception:
                continue
    except Exception:
        _log.exception("Cache inspector: could not list cache files")
    return sorted(entries, key=lambda e: e["key"])


def _delete_file_entry(store: Any, file_name: str) -> bool:
    """Delete one file-store entry by its on-disk name. Returns True if a file was removed."""
    if not _FILE_NAME_RE.match(file_name) or file_name in (".", ".."):
        return False
    root = store._path.resolve()
    removed = False
    for candidate in store._path.rglob(file_name):
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (ValueError, OSError):
            continue
        if resolved.is_file():
            resolved.unlink(missing_ok=True)
            removed = True
    return removed


def _truncate(value: Any, max_len: int = 120) -> str:
    s = json.dumps(value, default=str) if not isinstance(value, str) else value
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    Admin.require_tool(request, "cache")
    driver, entries = _list_entries()

    total = len(entries)
    try:
        page = max(1, int(request.query("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1
    last_page = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page = min(page, last_page)
    offset = (page - 1) * _PER_PAGE
    pagination = {
        "total": total,
        "per_page": _PER_PAGE,
        "current_page": page,
        "last_page": last_page,
        "from": offset + 1 if total else 0,
        "to": min(offset + _PER_PAGE, total),
    }

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": "Cache",
            "entries": entries[offset : offset + _PER_PAGE],
            "driver": driver,
            "count": total,
            "truncated": total >= _MAX_ENTRIES,
            "pagination": pagination,
        }
    )
    return Admin._render("admin/cache/index.html", ctx)


def delete(request: Request) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_tool
    from hunt.cache.manager import FileStore

    Admin.require_tool(request, "cache")
    key = str(request.input("key") or "")
    file_name = str(request.input("file") or "")
    if key or file_name:
        try:
            store = _store()
            if file_name and isinstance(store, FileStore):
                deleted = _delete_file_entry(store, file_name)
            else:
                store.forget(key)
                deleted = True
            if deleted:
                audit_tool("cache", "delete", key or file_name)
                _flash(request, "admin_success", f"Cache key '{key or file_name}' deleted.")
            else:
                _flash(request, "admin_error", "Cache key not found.")
        except Exception:
            _log.exception("Cache inspector: could not delete %r", key or file_name)
            _flash(request, "admin_error", "The cache key could not be deleted.")
    return RedirectResponse(f"{Admin.prefix}/cache")


def flush(request: Request) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_tool

    Admin.require_tool(request, "cache")
    try:
        _store().flush()
        audit_tool("cache", "flush")
        _flash(request, "admin_success", "Cache flushed.")
    except Exception:
        _log.exception("Cache inspector: could not flush the cache")
        _flash(request, "admin_error", "The cache could not be flushed.")
    return RedirectResponse(f"{Admin.prefix}/cache")
