from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

from hunt.http.request import Request
from hunt.http.response import Response


def _check_database() -> dict:
    try:
        from hunt.database.connection import raw

        start = time.monotonic()
        raw("SELECT 1").fetchone()
        ms = (time.monotonic() - start) * 1000
        return {"ok": True, "label": "Connected", "detail": f"{ms:.1f} ms"}
    except Exception as exc:
        return {"ok": False, "label": "Error", "detail": str(exc)[:120]}


def _check_cache() -> dict:
    try:
        from hunt.cache.manager import Cache

        store = Cache._get_store()
        key = "_hunt_health_probe"
        store.put(key, 1, 5)
        val = store.get(key)
        store.forget(key)
        if val != 1:
            return {"ok": False, "label": "Error", "detail": "Read-back mismatch"}
        driver = type(store).__name__.replace("Store", "").lower()
        return {"ok": True, "label": "Connected", "detail": driver}
    except Exception as exc:
        return {"ok": False, "label": "Error", "detail": str(exc)[:120]}


def _check_queue() -> dict:
    try:
        from hunt.database.connection import raw

        row = raw("SELECT COUNT(*) AS cnt FROM jobs WHERE reserved_at IS NULL").fetchone()
        pending = int(row.cnt) if row else 0
        return {"ok": True, "label": "OK", "detail": f"{pending} pending"}
    except Exception as exc:
        msg = str(exc)
        if "no such table" in msg.lower() or "doesn't exist" in msg.lower():
            return {"ok": None, "label": "Not configured", "detail": "jobs table missing — run migrations"}
        return {"ok": False, "label": "Error", "detail": msg[:120]}


def _storage_size(path: Path) -> str:
    try:
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        if total < 1024:
            return f"{total} B"
        if total < 1024**2:
            return f"{total / 1024:.1f} KB"
        if total < 1024**3:
            return f"{total / 1024**2:.1f} MB"
        return f"{total / 1024**3:.2f} GB"
    except Exception:
        return "unknown"


def index(request: Request) -> Response:
    from hunt import __version__
    from hunt.admin.application import Admin

    storage = Path.cwd() / "storage"

    checks = [
        {
            "name": "Database",
            "icon": "M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125",
            **_check_database(),
        },
        {
            "name": "Cache",
            "icon": "M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125",
            **_check_cache(),
        },
        {
            "name": "Queue",
            "icon": "M9 3.75H6.912a2.25 2.25 0 0 0-2.15 1.588L2.35 13.177a2.25 2.25 0 0 0-.1.661V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18v-4.162c0-.224-.034-.447-.1-.661L19.24 5.338a2.25 2.25 0 0 0-2.15-1.588H15M2.25 13.5h3.86a2.251 2.251 0 0 1 2.012 1.244l.256.512a2.252 2.252 0 0 0 2.013 1.244h3.218a2.252 2.252 0 0 0 2.013-1.244l.256-.512a2.251 2.251 0 0 1 2.012-1.244h3.860",
            **_check_queue(),
        },
    ]

    info = {
        "hunt_version": __version__,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.system(),
        "app_env": os.environ.get("APP_ENV", "production"),
        "app_debug": os.environ.get("APP_DEBUG", "false").lower() == "true",
        "storage_size": _storage_size(storage) if storage.is_dir() else "—",
        "log_size": _storage_size(storage / "logs") if (storage / "logs").is_dir() else "—",
    }

    overall_ok = all(c["ok"] is not False for c in checks)

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": "Health",
            "checks": checks,
            "info": info,
            "overall_ok": overall_ok,
        }
    )
    return Admin._render("admin/health/index.html", ctx)
