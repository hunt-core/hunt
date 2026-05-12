from __future__ import annotations

import os
from pathlib import Path

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

COOKIE_NAME = "hunt_session"


class StartSession(Middleware):
    async def handle(self, request: Request, next: Next) -> Response:
        from hunt.session.store import FileSessionStore
        from hunt.support.helpers import storage_path

        sessions_dir = Path(storage_path("framework", "sessions"))
        session_id = _read_cookie(request, COOKIE_NAME) or os.urandom(32).hex()

        store = FileSessionStore(sessions_dir)
        store.start(session_id)  # store validates/rejects malformed IDs internally
        store.age_flash()

        request._session = store  # type: ignore[attr-defined]

        response = await next(request)

        store.save()
        secure = request._scope.get("scheme", "http") == "https"
        _set_cookie(response, COOKIE_NAME, store.id, max_age=7200, http_only=True, secure=secure)
        return response


def _read_cookie(request: Request, name: str) -> str | None:
    header = request.header("cookie", "") or ""
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip() == name:
            return v.strip() or None
    return None


def _set_cookie(
    response: Response,
    name: str,
    value: str | None,
    *,
    max_age: int = 0,
    http_only: bool = False,
    secure: bool = False,
    same_site: str = "Lax",
    path: str = "/",
) -> None:
    if value is None:
        return
    cookie = f"{name}={value}; Path={path}; SameSite={same_site}"
    if max_age:
        cookie += f"; Max-Age={max_age}"
    if http_only:
        cookie += "; HttpOnly"
    if secure:
        cookie += "; Secure"
    response.add_cookie_header(cookie)
