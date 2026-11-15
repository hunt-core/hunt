from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response, _is_safe_redirect

_log = logging.getLogger("hunt.session")

COOKIE_NAME = "hunt_session"


def _make_session_store():
    from hunt.session import session_driver

    driver = session_driver()
    if driver == "redis":
        from hunt.session.redis_store import RedisSessionStore

        return RedisSessionStore()
    from hunt.session.store import FileSessionStore
    from hunt.support.helpers import storage_path

    return FileSessionStore(Path(storage_path("framework", "sessions")))


# Session keys that are bookkeeping only: a session holding nothing else is "empty".
_REGISTRY_TOUCH_KEY = "_registry_touched_at"
_REGISTRY_TOUCH_INTERVAL = 300  # seconds between last_active_at updates


class StartSession(Middleware):
    """Load the session before the request and persist it afterwards.

    * The store is read and written on a worker thread, so slow disk or Redis
      I/O does not block the event loop.
    * The session is saved in a ``finally`` block: whatever happens further
      in, flashed data and a regenerated ID are kept.
    * Sessions are lazy: a visitor without a session gets no cookie and no
      stored record until something is actually put in the session.
    * A publicly cacheable response never carries the session cookie.
    """

    reads_body = False  # never touches the request body

    async def handle(self, request: Request, next: Next) -> Response:
        session_id = _read_cookie(request, COOKIE_NAME) or os.urandom(32).hex()

        store = _make_session_store()
        # The store validates the ID and only adopts one it has a record for.
        await asyncio.to_thread(store.start, session_id)
        store.age_flash()

        request._session = store  # type: ignore[attr-defined]

        response: Response | None = None
        try:
            response = await next(request)
        finally:
            await self._finish(request, store, response)
        return response

    async def _finish(self, request: Request, store: Any, response: Response | None) -> None:
        existed = bool(getattr(store, "existed", True))
        regenerated = getattr(store, "started_id", store.id) != store.id
        if getattr(store, "read_failed", False):
            # The backend was unreachable at start(): leave the stored session
            # and the client's cookie exactly as they are.
            return

        if response is not None:
            _remember_previous_url(request, store, response, existed)

        if not existed and _is_empty(store):
            # Nothing was put in a brand-new session: do not create a record or
            # hand out a cookie for it.
            return

        cacheable = response is not None and _is_publicly_cacheable(response)
        if cacheable and (not existed or regenerated):
            # The handler changed session state on a response it marked public;
            # the cookie has to go out, so the response must not be shared.
            response.header("Cache-Control", "private, no-cache")  # type: ignore[union-attr]
            cacheable = False

        if existed and not regenerated:
            await self._touch_registry(store)

        try:
            saved = await asyncio.to_thread(store.save)
        except Exception as exc:
            _log.error("Session save failed: %s", exc)
            return
        if saved is False or response is None or cacheable:
            # Not stored (revoked meanwhile / backend error), or a response a
            # shared cache may keep: never attach the session cookie.
            return

        secure = _session_secure() or request._scope.get("scheme", "http") == "https"
        same_site = _session_same_site()
        lifetime = _session_lifetime()
        _set_cookie(
            response, COOKIE_NAME, store.id, max_age=lifetime, http_only=True, secure=secure, same_site=same_site
        )

    @staticmethod
    async def _touch_registry(store: Any) -> None:
        """Keep ``user_sessions.last_active_at`` current for signed-in sessions,
        at most once every few minutes per session."""
        try:
            data = store.all()
            if not any(str(key).startswith("_auth_id") for key in data):
                return
            now = int(time.time())
            last = data.get(_REGISTRY_TOUCH_KEY)
            if isinstance(last, (int, float)) and now - last < _REGISTRY_TOUCH_INTERVAL:
                return
            store.put(_REGISTRY_TOUCH_KEY, now)
            from hunt.session.registry import SessionRegistry

            await asyncio.to_thread(SessionRegistry().touch, store.id)
        except Exception as exc:
            _log.debug("Session registry touch skipped: %s", exc)


def _is_empty(store: Any) -> bool:
    """True when the session holds nothing but empty flash bags."""
    for key, value in store.all().items():
        if key in ("_flash_old", "_flash_new") and not value:
            continue
        return False
    return True


def _is_publicly_cacheable(response: Response) -> bool:
    cache_control = (response.get_header("Cache-Control") or "").lower()
    if not cache_control:
        return False
    directives = {part.strip().split("=", 1)[0] for part in cache_control.split(",")}
    if directives & {"private", "no-store", "no-cache"}:
        return False
    return bool(directives & {"public", "s-maxage"})


def _remember_previous_url(request: Request, store: Any, response: Response, existed: bool) -> None:
    """Record the page just served as the target of ``redirect().back()``.

    Only full-page views count: a successful GET answered with HTML that is
    neither an XHR/JSON call nor a prefetch. A session is never created just
    to hold this value.
    """
    if request.method != "GET" or not 200 <= response.status < 300:
        return
    if not existed and _is_empty(store):
        return
    if request.expects_json() or (request.header("x-requested-with", "") or "").lower() == "xmlhttprequest":
        return
    purpose = ((request.header("sec-purpose", "") or "") + (request.header("purpose", "") or "")).lower()
    if "prefetch" in purpose:
        return
    if "text/html" not in (response.content_type or "").lower():
        return
    url = request.path
    query = request._scope.get("query_string", b"") or b""
    if query:
        url += "?" + (query.decode("latin-1") if isinstance(query, bytes) else str(query))
    if len(url) <= 2048 and _is_safe_redirect(url):
        store.put("_previous_url", url)


def _session_secure() -> bool:
    """Whether the session cookie gets the ``Secure`` attribute.

    ``session.secure`` in config, else SESSION_SECURE, else on in production:
    behind a TLS-terminating proxy the app sees plain http, so the request
    scheme alone would leave the cookie without ``Secure``. Set
    SESSION_SECURE=false to serve a production app over plain http.
    """
    try:
        from hunt.support.helpers import config as _cfg

        val = _cfg("session.secure", None)
        if val is not None:
            return val if isinstance(val, bool) else str(val).lower() == "true"
    except Exception:
        pass
    env = os.environ.get("SESSION_SECURE", "").strip().lower()
    if env:
        return env in ("true", "1", "yes")
    from hunt.application import app_env

    return app_env() == "production"


def _session_lifetime() -> int:
    try:
        from hunt.support.helpers import config as _cfg

        return int(_cfg("session.lifetime", 7200))
    except Exception:
        return 7200


def _session_same_site() -> str:
    try:
        from hunt.support.helpers import config as _cfg

        val = _cfg("session.same_site", "Strict")
        if val in ("Strict", "Lax", "None"):
            return val
    except Exception:
        pass
    return "Strict"


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
