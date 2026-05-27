from __future__ import annotations

import json
import os
import re
from pathlib import Path

from hunt.http.request import Request
from hunt.http.response import Response

_TEXT_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] (\w+)\s+(.*)", re.DOTALL)
_LEVELS = ("debug", "info", "warning", "error", "critical")
_MAX_LINES = 2000
_PER_PAGE = 50


def _log_path() -> Path:
    raw = os.environ.get("LOG_PATH", "")
    if raw:
        return Path(raw)
    return Path.cwd() / "storage" / "logs" / "hunt.log"


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            obj = json.loads(line)
            return {
                "ts": obj.get("ts", ""),
                "level": obj.get("level", "info").lower(),
                "message": obj.get("message", ""),
                "request_id": obj.get("request_id") or "",
                "exception": obj.get("exception") or "",
            }
        except Exception:
            pass
    m = _TEXT_RE.match(line)
    if m:
        return {
            "ts": m.group(1),
            "level": m.group(2).lower(),
            "message": m.group(3),
            "request_id": "",
            "exception": "",
        }
    return None


def _tail(path: Path, n: int) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return []
        chunk = min(size, n * 200)
        f.seek(max(0, size - chunk))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.splitlines()
    return lines[-n:]


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    ctx = Admin._base_context(request)

    level_filter = (request.query("level") or "").lower()
    if level_filter not in _LEVELS:
        level_filter = ""
    search = (request.query("search") or "").strip().lower()

    try:
        page = max(1, int(request.query("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1

    log_path = _log_path()
    all_entries: list[dict] = []
    missing = not log_path.exists()

    if not missing:
        raw_lines = _tail(log_path, _MAX_LINES)
        for line in reversed(raw_lines):
            entry = _parse_line(line)
            if entry is None:
                continue
            if level_filter and entry["level"] != level_filter:
                continue
            if search and search not in entry["message"].lower() and search not in entry["request_id"].lower():
                continue
            all_entries.append(entry)

    total = len(all_entries)
    last_page = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page = min(page, last_page)
    offset = (page - 1) * _PER_PAGE
    entries = all_entries[offset : offset + _PER_PAGE]
    pagination = {
        "total": total,
        "per_page": _PER_PAGE,
        "current_page": page,
        "last_page": last_page,
        "from": offset + 1 if total else 0,
        "to": min(offset + _PER_PAGE, total),
    }

    ctx.update(
        {
            "title": "Logs",
            "entries": entries,
            "pagination": pagination,
            "missing": missing,
            "log_path": str(log_path),
            "level_filter": level_filter,
            "search": request.query("search") or "",
            "levels": _LEVELS,
        }
    )
    return Admin._render("admin/logs/index.html", ctx)
