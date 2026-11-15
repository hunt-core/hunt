from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

# Characters that may never appear in a header name or value: CR/LF would split
# the response, NUL is rejected by every server.
_HEADER_UNSAFE = {ord("\r"): None, ord("\n"): None, 0: None}
# RFC 6265 cookie-octet minus "%" (which we encode so the value round-trips
# through Request.cookie(), which percent-decodes).
_COOKIE_VALUE_SAFE = "!#$&'()*+-./:<=>?@[]^_`{|}~"
_COOKIE_NAME_UNSAFE = re.compile(r"[^!#$%&'*+\-.^_`|~0-9A-Za-z]")
_COOKIE_ATTR_UNSAFE = re.compile(r"[;,\x00-\x1f\x7f]")


def _clean_header(value: Any) -> str:
    """Drop CR, LF and NUL so a header can never split the response."""
    return str(value).translate(_HEADER_UNSAFE)


class HttpException(Exception):
    def __init__(self, status: int, message: str = "", headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.message = message
        self.headers: dict[str, str] = dict(headers or {})
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
        # Copy: the caller's dict must not be aliased (later header() calls
        # would otherwise leak into a dict shared between responses).
        self._headers: dict[str, str] = {}
        self._cookie_headers: list[str] = []
        if isinstance(content, str):
            self._body = content.encode("utf-8")
        else:
            self._body = content
        for key, value in (headers or {}).items():
            self.header(key, value)

    def header(self, key: str, value: str) -> Response:
        key = _clean_header(key)
        value = _clean_header(value)
        lowered = key.lower()
        if lowered == "content-type":
            # Content-Type is emitted once, from ``content_type``. The entry kept
            # in ``_headers`` is for introspection only; _asgi_headers skips it.
            self.content_type = value
        if key not in self._headers:
            # Header names are case-insensitive: replace, never duplicate.
            for existing in [k for k in self._headers if k.lower() == lowered]:
                del self._headers[existing]
        self._headers[key] = value
        return self

    def get_header(self, key: str, default: str | None = None) -> str | None:
        """Return a response header by case-insensitive name."""
        lowered = key.lower()
        if lowered == "content-type":
            return self.content_type
        for k, v in self._headers.items():
            if k.lower() == lowered:
                return v
        return default

    def vary(self, *values: str) -> Response:
        """Append to the Vary header without dropping values already present."""
        existing = self.get_header("Vary") or ""
        parts = [p.strip() for p in existing.split(",") if p.strip()]
        seen = {p.lower() for p in parts}
        for value in values:
            for item in str(value).split(","):
                item = item.strip()
                if item and item.lower() not in seen:
                    parts.append(item)
                    seen.add(item.lower())
        if parts:
            self.header("Vary", ", ".join(parts))
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
        # The name must be an RFC 6265 token and the value is percent-encoded, so
        # neither can inject an attribute (";"), a second cookie (",") or a new
        # header (CR/LF). Request.cookie() decodes the value again.
        name = _COOKIE_NAME_UNSAFE.sub("", str(name))
        if not name:
            raise ValueError("Cookie name is empty or contains only invalid characters.")
        value = quote(str(value), safe=_COOKIE_VALUE_SAFE)
        path = _COOKIE_ATTR_UNSAFE.sub("", str(path)) or "/"
        same_site = _COOKIE_ATTR_UNSAFE.sub("", str(same_site)) or "Lax"
        cookie = f"{name}={value}; Path={path}; SameSite={same_site}"
        if max_age:
            cookie += f"; Max-Age={int(max_age)}"
        if http_only:
            cookie += "; HttpOnly"
        if secure:
            cookie += "; Secure"
        self._cookie_headers.append(cookie)
        return self

    def with_etag(self, etag: str) -> Response:
        """Set the ETag response header. Wraps bare values in double-quotes."""
        value = etag if etag.startswith('"') or etag.startswith("W/") else f'"{etag}"'
        return self.header("ETag", value)

    def last_modified(self, dt: datetime | str) -> Response:
        """Set the Last-Modified header. Accepts a datetime or an RFC 7231 string."""
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            value = dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        else:
            value = str(dt)
        return self.header("Last-Modified", value)

    def cache(self, seconds: int, *, public: bool = True) -> Response:
        """Set Cache-Control with max-age."""
        scope = "public" if public else "private"
        return self.header("Cache-Control", f"{scope}, max-age={seconds}")

    def no_cache(self) -> Response:
        """Instruct clients and proxies never to cache this response."""
        self.header("Cache-Control", "no-store, no-cache, must-revalidate")
        return self.header("Pragma", "no-cache")

    def forget_cookie(self, name: str, path: str = "/") -> Response:
        name = _COOKIE_NAME_UNSAFE.sub("", str(name))
        path = _COOKIE_ATTR_UNSAFE.sub("", str(path)) or "/"
        self._cookie_headers.append(f"{name}=; Path={path}; Max-Age=0")
        return self

    def add_cookie_header(self, raw: str) -> None:
        self._cookie_headers.append(_clean_header(raw))

    def _asgi_headers(self) -> list[tuple[bytes, bytes]]:
        # Every value is cleaned again here: ``_headers`` / ``content_type`` can be
        # assigned directly, bypassing header().
        headers = [
            (b"content-type", _clean_header(self.content_type).encode("utf-8")),
            (b"content-length", str(len(self._body)).encode()),
        ]
        for k, v in self._headers.items():
            name = _clean_header(k).lower()
            if name in ("content-type", "content-length"):
                continue
            headers.append((name.encode("utf-8"), _clean_header(v).encode("utf-8")))
        for cookie in self._cookie_headers:
            headers.append((b"set-cookie", _clean_header(cookie).encode("utf-8")))
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
        self.header("Location", url)
        self.status = status
        return self

    def route(self, name: str, params: dict | None = None, status: int = 302) -> FluentRedirect:
        from hunt.support.helpers import app as _app

        router = _app("router")
        url = router.url(name, params or {})
        return self.to(url, status)

    def back(self, default: str = "/", status: int = 302) -> FluentRedirect:
        from hunt.auth.manager import _get_current_request

        return self.to(_previous_url(_get_current_request(), default), status)

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


_REDIRECT_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f\\]")


def _is_safe_redirect(url: str) -> bool:
    """Return True only for a same-site path: exactly one leading ``/``, no
    scheme or host, and no characters browsers rewrite into either.

    Browsers strip tab/CR/LF from URLs and treat ``\\`` as ``/``, so
    ``/\\evil.com`` and ``/\\t/evil.com`` both leave the site. Whitespace,
    control characters and backslashes are therefore rejected outright.
    """
    if not url or not isinstance(url, str):
        return False
    if not url.startswith("/") or url.startswith("//"):
        return False
    if _REDIRECT_UNSAFE_CHARS.search(url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return not parsed.netloc and not parsed.scheme


def _same_origin_path(url: str | None, request: Any) -> str | None:
    """Reduce *url* to a safe local path (plus query), or None.

    Accepts a URL that is already a safe local path, or an absolute http(s)
    URL whose host (and port) equals the request's ``Host`` header, which is
    what browsers send in ``Referer``. Anything else returns None.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if _is_safe_redirect(url):
        return url
    if _REDIRECT_UNSAFE_CHARS.search(url):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or "@" in parsed.netloc:
        return None
    host = ((request.header("host", "") if request is not None else "") or "").strip().lower()
    if not host or parsed.netloc.lower() != host:
        return None
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return path if _is_safe_redirect(path) else None


def _previous_url(request: Any, default: str = "/") -> str:
    """Where "back" points: the same-origin Referer, else the URL StartSession
    recorded for the last page view, else *default* (developer supplied, used
    as given). Request-derived values never leave the site."""
    if request is not None:
        referer = _same_origin_path(request.header("referer"), request)
        if referer:
            return referer
        store = getattr(request, "_session", None)
        if store is not None:
            stored = store.get("_previous_url")
            if isinstance(stored, str) and _is_safe_redirect(stored):
                return stored
    return default


def response(content: str = "", status: int = 200, headers: dict | None = None) -> Response:
    return Response(content, status, headers)


def json_response(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status)


def redirect(url: str = "/", status: int = 302) -> FluentRedirect:
    return FluentRedirect(url, status)
