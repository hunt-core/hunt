from __future__ import annotations

import datetime
from typing import Any

from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response


def _flash_and_redirect(request: Request, key: str, message: str, url: str) -> RedirectResponse:
    store = getattr(request, "_session", None)
    if store is not None:
        store.flash(key, message)
    return RedirectResponse(url)


def _fmt_time(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(int(ts), tz=datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def _short_ua(ua: str | None) -> str:
    if not ua:
        return "—"
    return ua[:120]


def index(request: Request) -> Response:
    from hunt.admin.application import Admin
    from hunt.session.registry import SessionRegistry

    try:
        page = max(1, int(request.query("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1

    per_page = 25
    reg = SessionRegistry()
    rows, total = reg.all_sessions(page=page, per_page=per_page)

    for row in rows:
        row["_fmt_time"] = _fmt_time(row.get("last_active_at"))
        row["_short_ua"] = _short_ua(row.get("user_agent"))

    pages = max(1, (total + per_page - 1) // per_page)
    pagination = {
        "total": total,
        "per_page": per_page,
        "current_page": page,
        "last_page": pages,
        "from": (page - 1) * per_page + 1 if total else 0,
        "to": min(page * per_page, total),
    }

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": "Sessions",
            "sessions": rows,
            "pagination": pagination,
        }
    )
    return Admin._render("admin/sessions/index.html", ctx)


def revoke(request: Request, session_id: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.session.registry import SessionRegistry

    reg = SessionRegistry()
    row = reg.get(session_id)
    if row is None:
        raise HttpException(404, "Session not found.")
    reg.revoke_session(session_id)
    return _flash_and_redirect(
        request,
        "admin_success",
        "Session revoked.",
        f"{Admin.prefix}/sessions",
    )


def revoke_user(request: Request, user_id: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.session.registry import SessionRegistry

    count = SessionRegistry().revoke_for_user(user_id)
    return _flash_and_redirect(
        request,
        "admin_success",
        f"Revoked {count} session(s) for user {user_id}.",
        f"{Admin.prefix}/sessions",
    )
