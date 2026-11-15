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


# Columns of a registry row that are safe to hand to the template. The row's
# ``id`` is normally sha256(session id) and equal to ``public_id``, but a row
# written before ids were hashed can still hold the session cookie value until
# it is migrated, so ``id`` is never passed on: only ``public_id`` is.
_SAFE_COLUMNS = ("public_id", "user_id", "guard", "ip_address", "user_agent", "last_active_at")


def _safe_row(row: dict) -> dict:
    safe = {key: row.get(key) for key in _SAFE_COLUMNS}
    public_id = safe.get("public_id")
    safe["public_id"] = str(public_id) if public_id not in (None, "") else ""
    safe["_fmt_time"] = _fmt_time(row.get("last_active_at"))
    safe["_short_ua"] = _short_ua(row.get("user_agent"))
    return safe


def index(request: Request) -> Response:
    from hunt.admin.application import Admin
    from hunt.session.registry import SessionRegistry

    Admin.require_tool(request, "sessions")

    try:
        page = max(1, int(request.query("page", "1") or "1"))
    except (ValueError, TypeError):
        page = 1

    per_page = 25
    reg = SessionRegistry()
    rows, total = reg.all_sessions(page=page, per_page=per_page)

    # Rebuild every row from an allow-list so a raw session id (a not yet
    # migrated row) cannot leak into the page, whatever the registry returns.
    sessions = [_safe_row(row) for row in rows]

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
            "sessions": sessions,
            "pagination": pagination,
        }
    )
    return Admin._render("admin/sessions/index.html", ctx)


def revoke(request: Request, public_id: str) -> Response:
    """Revoke one session, addressed by its non-secret public id only."""
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_tool
    from hunt.session.registry import SessionRegistry

    Admin.require_tool(request, "sessions")

    # A raw session id is never accepted here: only a public id matches.
    if not SessionRegistry().revoke_by_public_id(public_id):
        raise HttpException(404, "Session not found.")
    audit_tool("sessions", "revoke", public_id)
    return _flash_and_redirect(
        request,
        "admin_success",
        "Session revoked.",
        f"{Admin.prefix}/sessions",
    )


def revoke_user(request: Request, user_id: str) -> Response:
    from hunt.admin.application import Admin
    from hunt.admin.audit import audit_tool
    from hunt.session.registry import SessionRegistry

    Admin.require_tool(request, "sessions")

    count = SessionRegistry().revoke_for_user(user_id)
    audit_tool("sessions", "revoke_user", user_id, {"revoked": count})
    return _flash_and_redirect(
        request,
        "admin_success",
        f"Revoked {count} session(s) for user {user_id}.",
        f"{Admin.prefix}/sessions",
    )
