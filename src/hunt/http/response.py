from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse


class HttpException(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        self.message = message
        super().__init__(message)


class Response:
    def __init__(
        self,
        content: str | bytes = "",
        status: int = 200,
        headers: dict[str, str] | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.status = status
        self.content_type = content_type
        self._headers: dict[str, str] = headers or {}
        self._cookie_headers: list[str] = []
        if isinstance(content, str):
            self._body = content.encode("utf-8")
        else:
            self._body = content

    def header(self, key: str, value: str) -> Response:
        self._headers[key] = value
        return self

    def with_cookie(
        self,
        name: str,
        value: str,
        max_age: int = 0,
        path: str = "/",
        http_only: bool = True,
        secure: bool = False,
        same_site: str = "Lax",
    ) -> Response:
        cookie = f"{name}={value}; Path={path}; SameSite={same_site}"
        if max_age:
            cookie += f"; Max-Age={max_age}"
        if http_only:
            cookie += "; HttpOnly"
        if secure:
            cookie += "; Secure"
        self._cookie_headers.append(cookie)
        return self

    def with_etag(self, etag: str) -> Response:
        """Set the ETag response header. Wraps bare values in double-quotes."""
        value = etag if etag.startswith('"') or etag.startswith("W/") else f'"{etag}"'
        self._headers["ETag"] = value
        return self

    def last_modified(self, dt: datetime | str) -> Response:
        """Set the Last-Modified header. Accepts a datetime or an RFC 7231 string."""
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            value = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        else:
            value = str(dt)
        self._headers["Last-Modified"] = value
        return self

    def cache(self, seconds: int, *, public: bool = True) -> Response:
        """Set Cache-Control with max-age."""
        scope = "public" if public else "private"
        self._headers["Cache-Control"] = f"{scope}, max-age={seconds}"
        return self

    def no_cache(self) -> Response:
        """Instruct clients and proxies never to cache this response."""
        self._headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        self._headers["Pragma"] = "no-cache"
        return self

    def forget_cookie(self, name: str, path: str = "/") -> Response:
        self._cookie_headers.append(f"{name}=; Path={path}; Max-Age=0")
        return self

    def add_cookie_header(self, raw: str) -> None:
        self._cookie_headers.append(raw)

    def _asgi_headers(self) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"content-type", self.content_type.encode()),
            (b"content-length", str(len(self._body)).encode()),
        ]
        for k, v in self._headers.items():
            headers.append((k.lower().encode(), v.encode()))
        for cookie in self._cookie_headers:
            headers.append((b"set-cookie", cookie.encode()))
        return headers

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": self._asgi_headers(),
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": self._body,
            }
        )


class JsonResponse(Response):
    def __init__(self, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> None:
        super().__init__(
            content=json.dumps(data, default=str),
            status=status,
            headers=headers,
            content_type="application/json",
        )


class RedirectResponse(Response):
    def __init__(self, url: str, status: int = 302) -> None:
        super().__init__(content="", status=status, headers={"Location": url})


class FluentRedirect(RedirectResponse):
    """A redirect response with fluent flash-data helpers.

    redirect("/home").with_("status", "Saved!").with_errors(bag)
    redirect().route("dashboard")
    redirect().back()
    """

    def __init__(self, url: str = "/", status: int = 302) -> None:
        super().__init__(url, status)

    # ------------------------------------------------------------------
    # Destination helpers
    # ------------------------------------------------------------------

    def to(self, url: str, status: int = 302) -> FluentRedirect:
        self._headers["Location"] = url
        self.status = status
        return self

    def route(self, name: str, params: dict | None = None, status: int = 302) -> FluentRedirect:
        from hunt.support.helpers import app as _app

        router = _app("router")
        url = router.url(name, params or {})
        return self.to(url, status)

    def back(self, default: str = "/", status: int = 302) -> FluentRedirect:
        from hunt.auth.manager import _get_current_request

        req = _get_current_request()
        store = getattr(req, "_session", None) if req else None
        url = store.get("_previous_url", default) if store else default
        # Reject off-host redirects to prevent open-redirect attacks
        if not _is_safe_redirect(url):
            url = default
        return self.to(url, status)

    # ------------------------------------------------------------------
    # Flash data
    # ------------------------------------------------------------------

    def with_(self, key: str, value: Any) -> FluentRedirect:
        """Flash a single key/value to the session."""
        self._flash(key, value)
        return self

    def with_errors(self, errors: Any) -> FluentRedirect:
        """Flash a validation error bag (dict or ValidationException) to session."""
        from hunt.validation.validator import MessageBag, ValidationException

        if isinstance(errors, ValidationException):
            error_dict = errors.errors
        elif isinstance(errors, MessageBag):
            error_dict = errors._errors
        elif isinstance(errors, dict):
            error_dict = errors
        else:
            error_dict = {"error": [str(errors)]}
        self._flash("_errors", error_dict)
        return self

    def with_input(self) -> FluentRedirect:
        """Flash the current request's input to the session."""
        from hunt.auth.manager import _get_current_request

        req = _get_current_request()
        if req is not None:
            req.flash()
        return self

    def _flash(self, key: str, value: Any) -> None:
        from hunt.auth.manager import _get_current_request

        req = _get_current_request()
        store = getattr(req, "_session", None) if req else None
        if store is not None:
            store.flash(key, value)


def _is_safe_redirect(url: str) -> bool:
    """Return True if the URL is relative or same-host (no external redirect)."""
    if not url:
        return False
    # `///evil.com` parses to netloc="" but still redirects off-host in browsers
    if url.startswith("//"):
        return False
    parsed = urlparse(url)
    # Relative URLs have no scheme or netloc
    return not parsed.netloc and not parsed.scheme


def response(content: str = "", status: int = 200, headers: dict | None = None) -> Response:
    return Response(content, status, headers)


def json_response(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status)


def redirect(url: str = "/", status: int = 302) -> FluentRedirect:
    return FluentRedirect(url, status)
