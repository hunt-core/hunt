from __future__ import annotations

import json
import time
from typing import Any

from hunt.http.request import Request
from hunt.http.response import RedirectResponse, Response


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
            if cursor == 0:
                break
    except Exception:
        pass
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
    entries = []
    now = time.time()
    try:
        for f in store._path.rglob("*"):
            if not f.is_file():
                continue
            try:
                payload = json.loads(f.read_text())
                ea = payload.get("expires_at", 0)
                if ea and ea < now:
                    continue
                ttl = int(ea - now) if ea else None
                entries.append(
                    {
                        "key": f.name,
                        "ttl": ttl,
                        "value": _truncate(payload.get("value")),
                        "forever": ea == 0,
                    }
                )
            except Exception:
                continue
    except Exception:
        pass
    return sorted(entries, key=lambda e: e["key"])


def _truncate(value: Any, max_len: int = 120) -> str:
    s = json.dumps(value, default=str) if not isinstance(value, str) else value
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    driver, entries = _list_entries()
    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": "Cache",
            "entries": entries,
            "driver": driver,
            "count": len(entries),
        }
    )
    return Admin._render("admin/cache/index.html", ctx)


def delete(request: Request) -> Response:
    from hunt.admin.application import Admin

    key = request.input("key") or ""
    if key:
        try:
            _store().forget(key)
            _flash(request, "admin_success", f"Cache key '{key}' deleted.")
        except Exception as exc:
            _flash(request, "admin_error", str(exc))
    return RedirectResponse(f"{Admin.prefix}/cache")


def flush(request: Request) -> Response:
    from hunt.admin.application import Admin

    try:
        _store().flush()
        _flash(request, "admin_success", "Cache flushed.")
    except Exception as exc:
        _flash(request, "admin_error", str(exc))
    return RedirectResponse(f"{Admin.prefix}/cache")
