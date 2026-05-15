from __future__ import annotations

import fnmatch
import json as _json_mod
import time
from typing import Any

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover
    _httpx = None  # type: ignore[assignment]


class RequestException(Exception):
    """Raised by Response.throw() when the response indicates a failure."""

    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        super().__init__(f"HTTP request returned status {response.status_code}")


class HttpResponse:
    """Wraps an httpx response with a Laravel-flavoured API."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    # ------------------------------------------------------------------
    # Body
    # ------------------------------------------------------------------

    def body(self) -> str:
        return self._raw.text

    def text(self) -> str:
        return self._raw.text

    def json(self, key: str | None = None) -> Any:
        data = self._raw.json()
        if key is not None:
            return data.get(key)
        return data

    def content(self) -> bytes:
        return self._raw.content

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def status_code(self) -> int:
        return self._raw.status_code

    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def successful(self) -> bool:
        return self.ok()

    def redirect(self) -> bool:
        return 300 <= self.status_code < 400

    def failed(self) -> bool:
        return self.status_code >= 400

    def client_error(self) -> bool:
        return 400 <= self.status_code < 500

    def server_error(self) -> bool:
        return self.status_code >= 500

    def throw(self) -> HttpResponse:
        """Raise RequestException if the response failed."""
        if self.failed():
            raise RequestException(self)
        return self

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._raw.headers)

    def header(self, name: str, default: str | None = None) -> str | None:
        return self._raw.headers.get(name, default)

    # ------------------------------------------------------------------
    # Magic
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        return self.ok()

    def __repr__(self) -> str:
        return f"<HttpResponse [{self.status_code}]>"


class PendingRequest:
    """Fluent synchronous HTTP request builder.

    Obtain one via ``Http.with_headers()``, ``Http.timeout()``, etc., or
    call HTTP verbs directly on ``Http`` for a zero-config request.
    """

    def __init__(self, factory: _HttpFactory) -> None:
        self._factory = factory
        self._headers: dict[str, str] = {}
        self._timeout: float = 30.0
        self._retry_times: int = 0
        self._retry_sleep_ms: int = 100
        self._retry_when: Any = None
        self._body_format: str = "json"

    # ------------------------------------------------------------------
    # Builder
    # ------------------------------------------------------------------

    def with_headers(self, headers: dict[str, str]) -> PendingRequest:
        self._headers.update(headers)
        return self

    def with_token(self, token: str, type: str = "Bearer") -> PendingRequest:
        self._headers["Authorization"] = f"{type} {token}"
        return self

    def with_basic_auth(self, username: str, password: str) -> PendingRequest:
        import base64

        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers["Authorization"] = f"Basic {creds}"
        return self

    def accept(self, content_type: str) -> PendingRequest:
        self._headers["Accept"] = content_type
        return self

    def accept_json(self) -> PendingRequest:
        return self.accept("application/json")

    def as_json(self) -> PendingRequest:
        self._body_format = "json"
        return self

    def as_form(self) -> PendingRequest:
        self._body_format = "form"
        return self

    def timeout(self, seconds: float) -> PendingRequest:
        self._timeout = seconds
        return self

    def retry(self, times: int, sleep_ms: int = 100, when: Any = None) -> PendingRequest:
        """Retry on exception (or on condition) up to `times` additional attempts."""
        self._retry_times = times
        self._retry_sleep_ms = sleep_ms
        self._retry_when = when
        return self

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------

    def get(self, url: str, params: dict | None = None) -> HttpResponse:
        return self._send("GET", url, params=params)

    def post(self, url: str, data: Any = None) -> HttpResponse:
        return self._send("POST", url, data=data)

    def put(self, url: str, data: Any = None) -> HttpResponse:
        return self._send("PUT", url, data=data)

    def patch(self, url: str, data: Any = None) -> HttpResponse:
        return self._send("PATCH", url, data=data)

    def delete(self, url: str, params: dict | None = None) -> HttpResponse:
        return self._send("DELETE", url, params=params)

    def head(self, url: str, params: dict | None = None) -> HttpResponse:
        return self._send("HEAD", url, params=params)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send(self, method: str, url: str, **kwargs) -> HttpResponse:
        if self._factory._fakes is not None:
            resp = self._factory._match_fake(url)
            self._factory._recorded.append({"method": method.upper(), "url": url, "options": kwargs})
            return resp

        attempts = 0
        while True:
            try:
                resp = self._do_send(method, url, **kwargs)
                if self._retry_when is not None and self._retry_when(resp) and attempts < self._retry_times:
                    attempts += 1
                    if self._retry_sleep_ms:
                        time.sleep(self._retry_sleep_ms / 1000)
                    continue
                return resp
            except Exception:
                if attempts >= self._retry_times:
                    raise
                attempts += 1
                if self._retry_sleep_ms:
                    time.sleep(self._retry_sleep_ms / 1000)

    def _do_send(self, method: str, url: str, params: dict | None = None, data: Any = None) -> HttpResponse:
        kw: dict[str, Any] = {"headers": self._headers, "timeout": self._timeout}
        if params:
            kw["params"] = params
        if data is not None:
            if self._body_format == "json":
                kw["json"] = data
            else:
                kw["data"] = data
        with _httpx.Client() as client:
            raw = client.request(method, url, **kw)
        return HttpResponse(raw)


class _HttpFactory:
    """Global HTTP client facade with fake/testing support.

    Usage::

        from hunt.http.client import Http

        # Real requests
        resp = Http.get("https://api.example.com/users")
        resp = Http.with_token("my-token").post("https://api.example.com/users", {"name": "Alice"})

        # Fake mode
        Http.fake({"https://api.example.com/*": Http.response({"id": 1}, 201)})
        resp = Http.get("https://api.example.com/users/1")
        Http.assert_sent("https://api.example.com/*")
        Http.unfake()
    """

    def __init__(self) -> None:
        self._fakes: dict[str, HttpResponse] | None = None
        self._recorded: list[dict] = []

    # ------------------------------------------------------------------
    # Fake mode
    # ------------------------------------------------------------------

    def fake(self, responses: dict[str, HttpResponse] | None = None) -> _HttpFactory:
        """Enter fake mode. All requests are intercepted; no real I/O is performed.

        Pass a dict of ``{url_pattern: Http.response(...)}`` to stub specific URLs.
        Patterns may contain ``*`` wildcards. Omit or pass ``None`` to stub
        every request with an empty 200 response.
        """
        self._fakes = responses if responses is not None else {}
        self._recorded = []
        return self

    def unfake(self) -> _HttpFactory:
        """Exit fake mode and clear recorded requests."""
        self._fakes = None
        self._recorded = []
        return self

    @staticmethod
    def response(
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Build a fake HttpResponse for use with Http.fake(...)."""
        if isinstance(body, (dict, list)):
            content = _json_mod.dumps(body).encode()
            ct = "application/json"
        elif isinstance(body, bytes):
            content = body
            ct = "application/octet-stream"
        else:
            content = str(body or "").encode()
            ct = "text/plain"

        h: dict[str, str] = {"content-type": ct}
        h.update(headers or {})
        return HttpResponse(_httpx.Response(status, content=content, headers=h))

    def _match_fake(self, url: str) -> HttpResponse:
        """Return the stub response that best matches `url`."""
        if self._fakes:
            if url in self._fakes:
                return self._fakes[url]
            # Longest pattern first (most specific wins)
            for pattern in sorted(self._fakes, key=len, reverse=True):
                if fnmatch.fnmatch(url, pattern):
                    return self._fakes[pattern]
        return self.response("", 200)

    # ------------------------------------------------------------------
    # Recorded request assertions (use in fake mode)
    # ------------------------------------------------------------------

    def assert_sent(self, url_pattern: str | None = None, times: int | None = None) -> None:
        """Assert that at least one matching request was recorded."""
        matched = self._filter_recorded(url_pattern)
        if times is not None:
            assert len(matched) == times, f"Expected {times} request(s) matching {url_pattern!r}, got {len(matched)}"
        else:
            assert matched, f"Expected a request matching {url_pattern!r} to be sent, but none was"

    def assert_not_sent(self, url_pattern: str | None = None) -> None:
        """Assert that no matching request was recorded."""
        matched = self._filter_recorded(url_pattern)
        assert not matched, f"Expected no requests matching {url_pattern!r}, but {len(matched)} were sent"

    def assert_nothing_sent(self) -> None:
        """Assert that no requests at all were recorded."""
        assert not self._recorded, f"Expected no HTTP requests to be sent, but {len(self._recorded)} were recorded"

    def recorded(self, url_pattern: str | None = None) -> list[dict]:
        """Return recorded requests, optionally filtered by URL pattern."""
        return self._filter_recorded(url_pattern)

    def _filter_recorded(self, url_pattern: str | None) -> list[dict]:
        if url_pattern is None:
            return list(self._recorded)
        return [r for r in self._recorded if fnmatch.fnmatch(r["url"], url_pattern)]

    # ------------------------------------------------------------------
    # Convenience proxy — each call creates a fresh PendingRequest
    # ------------------------------------------------------------------

    def _pending(self) -> PendingRequest:
        return PendingRequest(self)

    def with_headers(self, headers: dict[str, str]) -> PendingRequest:
        return self._pending().with_headers(headers)

    def with_token(self, token: str, type: str = "Bearer") -> PendingRequest:
        return self._pending().with_token(token, type)

    def with_basic_auth(self, username: str, password: str) -> PendingRequest:
        return self._pending().with_basic_auth(username, password)

    def accept(self, content_type: str) -> PendingRequest:
        return self._pending().accept(content_type)

    def accept_json(self) -> PendingRequest:
        return self._pending().accept_json()

    def as_json(self) -> PendingRequest:
        return self._pending().as_json()

    def as_form(self) -> PendingRequest:
        return self._pending().as_form()

    def timeout(self, seconds: float) -> PendingRequest:
        return self._pending().timeout(seconds)

    def retry(self, times: int, sleep_ms: int = 100, when: Any = None) -> PendingRequest:
        return self._pending().retry(times, sleep_ms, when)

    def get(self, url: str, params: dict | None = None) -> HttpResponse:
        return self._pending().get(url, params)

    def post(self, url: str, data: Any = None) -> HttpResponse:
        return self._pending().post(url, data)

    def put(self, url: str, data: Any = None) -> HttpResponse:
        return self._pending().put(url, data)

    def patch(self, url: str, data: Any = None) -> HttpResponse:
        return self._pending().patch(url, data)

    def delete(self, url: str, params: dict | None = None) -> HttpResponse:
        return self._pending().delete(url, params)

    def head(self, url: str, params: dict | None = None) -> HttpResponse:
        return self._pending().head(url, params)


Http = _HttpFactory()
