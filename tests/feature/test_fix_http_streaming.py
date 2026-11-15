"""The request body is lazy: routing, throttling and authentication happen before it is read (HTTP-07).

Every test drives ``HttpKernel`` through a raw ASGI call whose ``receive`` counts its calls.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode

import pytest

from hunt.http.kernel import HttpKernel
from hunt.http.middleware import Middleware
from hunt.http.middleware.authenticate import Authenticate
from hunt.http.middleware.csrf import VerifyCsrfToken
from hunt.http.middleware.secure_headers import SecureHeaders
from hunt.http.middleware.session import COOKIE_NAME, StartSession
from hunt.http.middleware.throttle import ThrottleRequests
from hunt.http.request import ClientDisconnected, Request
from hunt.http.response import HttpException, JsonResponse, Response
from hunt.http.router import Router
from hunt.session.store import FileSessionStore
from hunt.validation.form_request import FormRequest

FORM = {"content-type": "application/x-www-form-urlencoded"}
JSON = {"content-type": "application/json"}


class Harness:
    """One ASGI exchange. ``receive`` hands out ``messages`` in order and counts its calls."""

    def __init__(self, messages: list[dict] | None = None, body: bytes = b"") -> None:
        self.messages = (
            list(messages) if messages is not None else [{"type": "http.request", "body": body, "more_body": False}]
        )
        self.receive_calls = 0
        self.delivered: list[dict] = []
        self.sent: list[dict] = []
        #: When set, the message at this index is only handed out once ``gate`` is set.
        self.hold_index: int | None = None
        self.gate = asyncio.Event()

    async def receive(self) -> dict:
        self.receive_calls += 1
        if self.hold_index is not None and len(self.delivered) == self.hold_index:
            await asyncio.wait_for(self.gate.wait(), timeout=5)
        if not self.messages:
            raise AssertionError("receive() called after the end of the body or a disconnect")
        message = self.messages.pop(0)
        self.delivered.append(message)
        return message

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    @property
    def status(self) -> int | None:
        return self.sent[0]["status"] if self.sent else None

    @property
    def headers(self) -> dict[str, str]:
        if not self.sent:
            return {}
        return {k.decode().lower(): v.decode() for k, v in self.sent[0].get("headers", [])}

    @property
    def body(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")

    def cookie(self, name: str = COOKIE_NAME) -> str | None:
        for key, value in self.sent[0].get("headers", []) if self.sent else []:
            if key.lower() == b"set-cookie" and value.decode().startswith(name + "="):
                return value.decode().split(";", 1)[0].split("=", 1)[1]
        return None


async def call(
    kernel: HttpKernel,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    messages: list[dict] | None = None,
    content_length: bool = True,
    harness: Harness | None = None,
) -> Harness:
    h = harness or Harness(messages, body)
    hdrs = {"host": "app.test", **{k.lower(): v for k, v in (headers or {}).items()}}
    if content_length and messages is None and harness is None and "content-length" not in hdrs:
        hdrs["content-length"] = str(len(body))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
        "scheme": "http",
        "server": ("0.0.0.0", 8000),
        "client": ("203.0.113.9", 4000),
    }
    await kernel(scope, h.receive, h.send)
    return h


def chunks(*parts: bytes) -> list[dict]:
    return [{"type": "http.request", "body": part, "more_body": i < len(parts) - 1} for i, part in enumerate(parts)]


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_LOG", "false")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("MAX_BODY_SIZE", raising=False)
    monkeypatch.delenv("OFFLOAD_SYNC_HANDLERS", raising=False)
    monkeypatch.delenv("SESSION_SECURE", raising=False)
    sessions = tmp_path / "sessions"
    monkeypatch.setattr("hunt.http.middleware.session._make_session_store", lambda: FileSessionStore(sessions))
    monkeypatch.chdir(tmp_path)
    from hunt.http.middleware import throttle

    throttle._hits.clear()
    throttle._expires.clear()
    yield
    throttle._hits.clear()
    throttle._expires.clear()


def _echo(calls: list):
    def handler(request):
        calls.append(request.body)
        return JsonResponse({"input": request.all(), "method": request.method})

    return handler


# ---------------------------------------------------------------------------
# Routing happens before the body is read
# ---------------------------------------------------------------------------


class TestRouteFirst:
    def _kernel(self, calls: list) -> HttpKernel:
        router = Router()
        router.post("/items", _echo(calls))
        return HttpKernel(router, global_middleware=[SecureHeaders])

    async def test_404_does_not_read_the_body(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/nowhere", headers=FORM, body=b"a=1")
        assert res.status == 404
        assert res.receive_calls == 0
        assert res.headers["x-content-type-options"] == "nosniff"  # still went through global middleware

    async def test_405_does_not_read_the_body(self):
        calls: list = []
        res = await call(self._kernel(calls), "PUT", "/items", headers=JSON, body=b'{"a": 1}')
        assert res.status == 405
        assert "POST" in res.headers["allow"]
        assert res.receive_calls == 0 and calls == []

    async def test_matched_route_reads_the_body_for_the_handler(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/items", headers=FORM, body=b"a=1&b=2")
        assert res.status == 200
        assert json.loads(res.body)["input"] == {"a": "1", "b": "2"}
        assert res.receive_calls == 1 and calls == [b"a=1&b=2"]

    async def test_receive_is_not_called_after_the_last_chunk(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/items", headers=FORM, messages=chunks(b"a=1", b"&b=2"))
        assert res.status == 200
        assert calls == [b"a=1&b=2"]
        assert res.receive_calls == 2  # the harness raises if receive() is called once more


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------


class TestLimits:
    def _kernel(self, calls: list) -> HttpKernel:
        router = Router()
        router.post("/upload", _echo(calls))
        router.post("/small", _echo(calls)).max_body(4)
        router.post("/big", _echo(calls)).max_body(64)
        return HttpKernel(router, global_middleware=[SecureHeaders])

    async def test_content_length_over_the_limit_is_refused_unread(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "10")
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/upload", body=b"x" * 11)
        assert res.status == 413
        assert res.receive_calls == 0 and calls == []
        assert res.headers["connection"] == "close"
        assert res.headers["x-frame-options"] == "SAMEORIGIN"  # passed through the global middleware

    async def test_chunked_overflow_is_a_413_while_reading(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "10")
        calls: list = []
        messages = chunks(b"x" * 6, b"x" * 6, b"x" * 6)
        res = await call(self._kernel(calls), "POST", "/upload", messages=messages)
        assert res.status == 413
        assert calls == []
        assert res.receive_calls == 2  # stopped at the chunk that crossed the limit
        assert res.headers["connection"] == "close"
        assert res.headers["x-frame-options"] == "SAMEORIGIN"

    async def test_body_at_the_limit_is_accepted(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "10")
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/upload", body=b"x" * 10)
        assert res.status == 200 and calls == [b"x" * 10]

    async def test_route_limit_smaller_than_the_global_one(self):
        calls: list = []
        kernel = self._kernel(calls)
        res = await call(kernel, "POST", "/small", body=b"12345")
        assert res.status == 413 and res.receive_calls == 0
        res = await call(kernel, "POST", "/small", messages=chunks(b"123", b"45"))
        assert res.status == 413
        res = await call(kernel, "POST", "/small", body=b"1234")
        assert res.status == 200
        assert calls == [b"1234"]

    async def test_route_limit_larger_than_the_global_one(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "10")
        calls: list = []
        kernel = self._kernel(calls)
        res = await call(kernel, "POST", "/big", body=b"x" * 40)
        assert res.status == 200
        res = await call(kernel, "POST", "/big", messages=chunks(b"x" * 20, b"x" * 20))
        assert res.status == 200
        assert calls == [b"x" * 40, b"x" * 40]
        res = await call(kernel, "POST", "/big", body=b"x" * 65)
        assert res.status == 413 and res.receive_calls == 0
        res = await call(kernel, "POST", "/upload", body=b"x" * 40)
        assert res.status == 413  # other routes keep the global limit

    def test_max_body_rejects_nonsense(self):
        route = Router().post("/x", lambda request: Response("ok"))
        with pytest.raises(ValueError):
            route.max_body(-1)
        with pytest.raises(ValueError):
            route.max_body("10")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Middleware that rejects a request does so before the body is read
# ---------------------------------------------------------------------------


class OneHit(ThrottleRequests):
    max_attempts = 1
    decay_seconds = 60


class KeyedByInput(ThrottleRequests):
    max_attempts = 1
    decay_seconds = 60

    def _resolve_key(self, request) -> str:
        return f"login:{request.input('email')}"


class TestRejectingMiddleware:
    async def test_throttled_request_is_answered_without_reading(self):
        calls: list = []
        router = Router()
        router.post("/login", _echo(calls)).middleware(OneHit)
        kernel = HttpKernel(router)
        first = await call(kernel, "POST", "/login", headers=FORM, body=b"a=1")
        assert first.status == 200 and first.receive_calls == 1
        second = await call(kernel, "POST", "/login", headers=FORM, body=b"a=1")
        assert second.status == 429
        assert second.headers["retry-after"]
        assert second.receive_calls == 0
        assert len(calls) == 1

    async def test_throttle_with_a_custom_key_still_sees_input(self):
        calls: list = []
        router = Router()
        router.post("/login", _echo(calls)).middleware(KeyedByInput)
        kernel = HttpKernel(router)
        assert (await call(kernel, "POST", "/login", headers=FORM, body=b"email=a%40x.test")).status == 200
        assert (await call(kernel, "POST", "/login", headers=FORM, body=b"email=b%40x.test")).status == 200
        assert (await call(kernel, "POST", "/login", headers=FORM, body=b"email=a%40x.test")).status == 429

    async def test_guest_is_turned_away_without_reading(self, monkeypatch):
        from hunt.auth import manager

        monkeypatch.setattr(manager.Auth, "check", lambda *a, **k: False, raising=False)
        calls: list = []
        router = Router()
        router.post("/account", _echo(calls)).middleware(Authenticate)
        kernel = HttpKernel(router)

        res = await call(kernel, "POST", "/account", headers={**JSON, "accept": "application/json"}, body=b"{}")
        assert res.status == 401 and res.receive_calls == 0

        res = await call(kernel, "POST", "/account", headers=FORM, body=b"a=1")
        assert res.status == 302 and res.headers["location"] == "/login"
        assert res.receive_calls == 0
        assert calls == []


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


class BodyProbe(Middleware):
    """Sits right behind CSRF and records whether the body had been read by then."""

    reads_body = False
    seen: list = []  # noqa: RUF012

    async def handle(self, request, next):
        BodyProbe.seen.append(request.body_loaded)
        return await next(request)


class TestCsrf:
    def _kernel(self, calls: list) -> HttpKernel:
        BodyProbe.seen = []
        router = Router()
        router.get("/form", lambda request: Response(request.session().csrf_token()))
        router.post("/save", _echo(calls))
        return HttpKernel(router, global_middleware=[StartSession, VerifyCsrfToken, BodyProbe])

    async def _token(self, kernel: HttpKernel) -> tuple[str, str]:
        res = await call(kernel, "GET", "/form")
        cookie = res.cookie()
        assert res.status == 200 and cookie
        return f"{COOKIE_NAME}={cookie}", res.body.decode()

    async def test_header_token_is_checked_without_reading_the_body(self):
        calls: list = []
        kernel = self._kernel(calls)
        cookie, token = await self._token(kernel)
        BodyProbe.seen = []
        res = await call(
            kernel, "POST", "/save", headers={**FORM, "cookie": cookie, "x-csrf-token": token}, body=b"name=ada"
        )
        assert res.status == 200
        assert BodyProbe.seen == [False]  # CSRF passed on the header alone
        assert json.loads(res.body)["input"] == {"name": "ada"}  # and the handler still got the body
        assert res.receive_calls == 1

    async def test_form_token_works(self):
        calls: list = []
        kernel = self._kernel(calls)
        cookie, token = await self._token(kernel)
        BodyProbe.seen = []
        body = urlencode({"_token": token, "name": "ada"}).encode()
        res = await call(kernel, "POST", "/save", headers={**FORM, "cookie": cookie}, body=body)
        assert res.status == 200
        assert BodyProbe.seen == [True]
        assert json.loads(res.body)["input"]["name"] == "ada"

    async def test_wrong_header_falls_back_to_the_form_token(self):
        calls: list = []
        kernel = self._kernel(calls)
        cookie, token = await self._token(kernel)
        body = urlencode({"_token": token}).encode()
        res = await call(kernel, "POST", "/save", headers={**FORM, "cookie": cookie, "x-csrf-token": "x"}, body=body)
        assert res.status == 200

    async def test_missing_token_is_a_419(self):
        calls: list = []
        kernel = self._kernel(calls)
        cookie, _ = await self._token(kernel)
        res = await call(kernel, "POST", "/save", headers={**FORM, "cookie": cookie}, body=b"name=ada")
        assert res.status == 419
        assert calls == []

    async def test_wrong_tokens_are_a_419(self):
        calls: list = []
        kernel = self._kernel(calls)
        cookie, _ = await self._token(kernel)
        res = await call(
            kernel, "POST", "/save", headers={**FORM, "cookie": cookie, "x-csrf-token": "nope"}, body=b"_token=nope"
        )
        assert res.status == 419 and calls == []

    async def test_without_a_session_token_the_body_is_not_read(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/save", headers=FORM, body=b"_token=x")
        assert res.status == 419
        assert res.receive_calls == 0


# ---------------------------------------------------------------------------
# reads_body
# ---------------------------------------------------------------------------


class LegacyMiddleware(Middleware):
    """Written before lazy bodies existed: uses input, declares nothing."""

    seen: list = []  # noqa: RUF012

    async def handle(self, request, next):
        LegacyMiddleware.seen.append((request.input("name"), request.body))
        return await next(request)


class Forgetful(Middleware):
    reads_body = False

    async def handle(self, request, next):
        request.input("name")
        return await next(request)


class LoadsItself(Middleware):
    reads_body = False

    async def handle(self, request, next):
        if request.header("x-inspect"):
            await request.load_body()
            if request.input("name") == "blocked":
                raise HttpException(403, "No.")
        return await next(request)


class TestReadsBody:
    async def test_middleware_without_the_attribute_sees_input(self):
        LegacyMiddleware.seen = []
        calls: list = []
        router = Router()
        router.post("/save", _echo(calls)).middleware(LegacyMiddleware)
        res = await call(HttpKernel(router), "POST", "/save", headers=FORM, body=b"name=ada")
        assert res.status == 200
        assert LegacyMiddleware.seen == [("ada", b"name=ada")]

    async def test_legacy_global_middleware_sees_json(self):
        LegacyMiddleware.seen = []
        calls: list = []
        router = Router()
        router.post("/save", _echo(calls))
        kernel = HttpKernel(router, global_middleware=[LegacyMiddleware])
        res = await call(kernel, "POST", "/save", headers=JSON, body=b'{"name": "ada"}')
        assert res.status == 200
        assert LegacyMiddleware.seen == [("ada", b'{"name": "ada"}')]

    async def test_sync_accessor_before_load_raises_a_clear_error(self):
        request = Request({"type": "http", "method": "POST", "headers": []}, receive=Harness(body=b"a=1").receive)
        assert request.body_loaded is False
        for access in (
            lambda: request.body,
            lambda: request.input("a"),
            request.all,
            lambda: request.has("a"),
            request.json,
            lambda: request.file("a"),
            lambda: request.files("a"),
        ):
            with pytest.raises(RuntimeError, match=r"await request\.load_body\(\)") as err:
                access()
            assert "reads_body = False" in str(err.value)
        assert await request.load_body() == b"a=1"
        assert await request.load_body() == b"a=1"  # idempotent: receive() is not called again
        assert request.input("a") == "1"

    async def test_reads_body_false_middleware_using_input_fails_loudly(self, monkeypatch):
        logged: list = []
        monkeypatch.setattr("hunt.log.manager.Log.exception", lambda message, **kw: logged.append(kw.get("exc")))
        calls: list = []
        router = Router()
        router.post("/save", _echo(calls)).middleware(Forgetful)
        res = await call(HttpKernel(router), "POST", "/save", headers=FORM, body=b"name=ada")
        assert res.status == 500
        assert calls == []
        assert len(logged) == 1 and isinstance(logged[0], RuntimeError)
        assert "load_body" in str(logged[0])

    async def test_reads_body_false_middleware_may_load_the_body_itself(self):
        calls: list = []
        router = Router()
        router.post("/save", _echo(calls)).middleware(LoadsItself)
        kernel = HttpKernel(router)
        res = await call(kernel, "POST", "/save", headers={**FORM, "x-inspect": "1"}, body=b"name=blocked")
        assert res.status == 403
        res = await call(kernel, "POST", "/save", headers={**FORM, "x-inspect": "1"}, body=b"name=ada")
        assert res.status == 200 and res.receive_calls == 1

    def test_request_with_a_ready_body_is_loaded(self):
        request = Request(
            {"type": "http", "method": "POST", "headers": [(b"content-type", b"application/json")]}, b"{}"
        )
        assert request.body_loaded is True
        assert request.body == b"{}" and request.all() == {}

    def test_framework_middleware_flags(self):
        from hunt.admin.middleware.csrf import AdminVerifyCsrfToken
        from hunt.http.middleware.compress import CompressResponse
        from hunt.http.middleware.cors import HandleCors
        from hunt.http.middleware.debug_panel import DebugPanel
        from hunt.http.middleware.maintenance import MaintenanceMode
        from hunt.http.middleware.request_id import RequestId
        from hunt.http.middleware.throttle import RedisThrottleRequests
        from hunt.http.middleware.trust_proxies import TrustProxies
        from hunt.http.middleware.two_factor import EnsureTwoFactorAuthenticated
        from hunt.http.middleware.verified import EnsureEmailIsVerified

        assert Middleware.reads_body is True
        for cls in (
            MaintenanceMode,
            TrustProxies,
            RequestId,
            SecureHeaders,
            HandleCors,
            StartSession,
            ThrottleRequests,
            RedisThrottleRequests,
            Authenticate,
            EnsureEmailIsVerified,
            EnsureTwoFactorAuthenticated,
            CompressResponse,
            DebugPanel,
            VerifyCsrfToken,
            AdminVerifyCsrfToken,
        ):
            assert cls.reads_body is False, cls


# ---------------------------------------------------------------------------
# _method override
# ---------------------------------------------------------------------------


class TestMethodOverride:
    def _kernel(self, calls: list) -> HttpKernel:
        router = Router()
        router.put("/posts/{id}", _echo(calls))
        router.post("/posts", _echo(calls)).middleware(OneHit)
        router.post("/mixed", _echo(calls)).max_body(64)
        router.delete("/mixed", _echo(calls))
        return HttpKernel(router)

    async def test_override_reaches_the_put_route(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/posts/7", headers=FORM, body=b"_method=PUT&title=x")
        assert res.status == 200
        assert json.loads(res.body) == {"input": {"_method": "PUT", "title": "x", "id": "7"}, "method": "PUT"}
        assert res.receive_calls == 1

    async def test_multipart_override(self):
        calls: list = []
        boundary = "XbX"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="_method"\r\n\r\nput\r\n--{boundary}--\r\n'
        ).encode()
        headers = {"content-type": f"multipart/form-data; boundary={boundary}"}
        res = await call(self._kernel(calls), "POST", "/posts/7", headers=headers, body=body)
        assert res.status == 200 and json.loads(res.body)["method"] == "PUT"

    async def test_post_without_the_field_to_a_put_only_path_is_405(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/posts/7", headers=FORM, body=b"title=x")
        assert res.status == 405 and calls == []

    async def test_json_post_is_never_read_early(self):
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/posts/7", headers=JSON, body=b'{"_method": "PUT"}')
        assert res.status == 405
        assert res.receive_calls == 0

    async def test_post_only_path_is_not_read_before_routing(self):
        calls: list = []
        kernel = self._kernel(calls)
        first = await call(kernel, "POST", "/posts", headers=FORM, body=b"_method=PUT")
        assert first.status == 200 and json.loads(first.body)["method"] == "POST"
        # Over the throttle now: were the body read before routing, receive() would have been called.
        res = await call(kernel, "POST", "/posts", headers=FORM, body=b"_method=PUT")
        assert res.status == 429
        assert res.receive_calls == 0
        assert calls == [b"_method=PUT"]

    async def test_early_read_is_still_limited(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "16")
        calls: list = []
        kernel = self._kernel(calls)
        res = await call(kernel, "POST", "/posts/7", headers=FORM, body=b"_method=PUT&title=" + b"x" * 40)
        assert res.status == 413 and res.receive_calls == 0
        res = await call(kernel, "POST", "/posts/7", headers=FORM, messages=chunks(b"_method=PUT&", b"t=" + b"x" * 40))
        assert res.status == 413 and calls == []

    async def test_early_read_uses_the_largest_candidate_limit_then_the_route_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "16")
        calls: list = []
        kernel = self._kernel(calls)
        # POST /mixed allows 64 bytes; DELETE /mixed only the global 16.
        res = await call(kernel, "POST", "/mixed", headers=FORM, body=b"title=" + b"x" * 30)
        assert res.status == 200
        res = await call(kernel, "POST", "/mixed", headers=FORM, messages=chunks(b"_method=DELETE&t=" + b"x" * 30))
        assert res.status == 413
        res = await call(kernel, "POST", "/mixed", headers=FORM, body=b"_method=DELETE")
        assert res.status == 200 and json.loads(res.body)["method"] == "DELETE"

    def test_router_helper(self):
        router = Router()
        router.put("/posts/{id}", lambda request: None)
        router.post("/posts", lambda request: None)
        with router.group(domain="api.example.com"):
            router.delete("/keys", lambda request: None)
        assert [r.uri for r in router.method_override_targets("/posts/3")] == ["/posts/{id}"]
        assert [r.uri for r in router.method_override_targets("/posts/3/")] == ["/posts/{id}"]
        assert router.method_override_targets("/posts") == []
        assert router.method_override_targets("/keys", "app.test") == []
        assert len(router.method_override_targets("/keys", "api.example.com")) == 1


# ---------------------------------------------------------------------------
# stream_body()
# ---------------------------------------------------------------------------


class TestStreamBody:
    async def test_handler_receives_chunks_as_they_arrive(self):
        harness = Harness(chunks(b"first", b"second", b"last"))
        harness.hold_index = 2  # the last chunk is held back until the handler has seen the first
        seen: list = []
        router = Router()

        async def upload(request):
            assert request.body_loaded is False
            async for chunk in request.stream():
                seen.append((chunk, len(harness.delivered)))
                harness.gate.set()
            # Fully consumed: these must return at once instead of waiting on receive().
            assert await request.load_body() == b""
            assert request.body == b""
            return Response(str(sum(len(c) for c, _ in seen)))

        router.post("/upload", upload).stream_body()
        res = await call(HttpKernel(router), "POST", "/upload", harness=harness)
        assert res.status == 200 and res.body == b"15"
        assert [c for c, _ in seen] == [b"first", b"second", b"last"]
        assert seen[0][1] == 1  # the first chunk was handled when only one message had been delivered
        assert res.receive_calls == 3

    async def test_body_is_not_loaded_before_the_handler(self):
        router = Router()
        state: dict = {}

        async def upload(request):
            state["loaded"] = request.body_loaded
            try:
                request.input("a")
            except RuntimeError as exc:
                state["error"] = str(exc)
            await request.load_body()
            return Response(request.input("a"))

        router.post("/upload", upload).stream_body()
        res = await call(HttpKernel(router), "POST", "/upload", headers=FORM, body=b"a=1")
        assert res.status == 200 and res.body == b"1"
        assert state["loaded"] is False and "request.stream()" in state["error"]

    async def test_limit_is_enforced_while_streaming(self):
        seen: list = []
        router = Router()

        async def upload(request):
            async for chunk in request.stream():
                seen.append(chunk)
            return Response("done")

        router.post("/upload", upload).stream_body().max_body(8)
        kernel = HttpKernel(router, global_middleware=[SecureHeaders])
        res = await call(kernel, "POST", "/upload", messages=chunks(b"12345", b"6789", b"more"))
        assert res.status == 413
        assert seen == [b"12345"]
        assert res.receive_calls == 2
        assert res.headers["x-frame-options"] == "SAMEORIGIN"
        res = await call(kernel, "POST", "/upload", body=b"123456789")
        assert res.status == 413 and res.receive_calls == 0

    async def test_disconnect_mid_stream_aborts(self):
        seen: list = []
        finished: list = []
        router = Router()

        async def upload(request):
            async for chunk in request.stream():
                seen.append(chunk)
            finished.append(True)
            return Response("done")

        router.post("/upload", upload).stream_body()
        messages = [{"type": "http.request", "body": b"abc", "more_body": True}, {"type": "http.disconnect"}]
        res = await call(HttpKernel(router), "POST", "/upload", messages=messages)
        assert seen == [b"abc"] and finished == []
        assert res.sent == []
        assert res.receive_calls == 2

    async def test_stream_is_usable_once(self):
        router = Router()

        async def upload(request):
            first = [c async for c in request.stream()]
            try:
                [c async for c in request.stream()]
            except RuntimeError as exc:
                return Response(f"{b''.join(first).decode()}|{exc}")
            return Response("no error")

        router.post("/upload", upload).stream_body()
        res = await call(HttpKernel(router), "POST", "/upload", messages=chunks(b"ab", b"cd"))
        assert res.body.startswith(b"abcd|") and b"once" in res.body

    async def test_stream_after_load_yields_the_buffered_body_once(self):
        router = Router()

        async def normal(request):
            return Response(b"|".join([c async for c in request.stream()]))

        router.post("/normal", normal)  # not a stream_body route: the body is loaded already
        res = await call(HttpKernel(router), "POST", "/normal", messages=chunks(b"ab", b"cd"))
        assert res.status == 200 and res.body == b"abcd"

    async def test_load_body_after_a_partial_stream_returns_the_rest(self):
        router = Router()

        async def upload(request):
            async for chunk in request.stream():
                assert chunk == b"ab"
                break
            return Response(await request.load_body())

        router.post("/upload", upload).stream_body()
        res = await call(HttpKernel(router), "POST", "/upload", messages=chunks(b"ab", b"cd", b"ef"))
        assert res.status == 200 and res.body == b"cdef"

    def test_sync_handler_is_refused_at_registration(self):
        router = Router()
        with pytest.raises(TypeError, match="async def"):
            router.post("/upload", lambda request: Response("x")).stream_body()

        async def ok(request):
            return Response("x")

        assert router.post("/ok", ok).stream_body()._stream_body is True


# ---------------------------------------------------------------------------
# Disconnects
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_disconnect_mid_body_does_not_call_the_handler(self):
        calls: list = []
        router = Router()
        router.post("/save", _echo(calls))
        kernel = HttpKernel(router, global_middleware=[SecureHeaders, StartSession])
        messages = [{"type": "http.request", "body": b"a=", "more_body": True}, {"type": "http.disconnect"}]
        res = await call(kernel, "POST", "/save", headers=FORM, messages=messages)
        assert calls == [] and res.sent == []
        assert res.receive_calls == 2  # nothing is received after the disconnect

    async def test_disconnect_is_not_swallowed_by_error_handling(self):
        request = Request({"type": "http", "method": "POST", "headers": []}, receive=Harness([]).receive)

        async def gone():
            return {"type": "http.disconnect"}

        request._receive = gone
        with pytest.raises(ClientDisconnected):
            await request.load_body()
        with pytest.raises(ClientDisconnected):  # and receive() is not called a second time
            await request.load_body()


# ---------------------------------------------------------------------------
# Handlers still get complete bodies
# ---------------------------------------------------------------------------


class NameForm(FormRequest):
    def rules(self) -> dict:
        return {"name": "required"}


class TestHandlersSeeTheBody:
    async def test_json(self):
        calls: list = []
        router = Router()
        router.post("/save", _echo(calls))
        res = await call(HttpKernel(router), "POST", "/save", headers=JSON, messages=chunks(b'{"a": [1, ', b"2]}"))
        assert json.loads(res.body)["input"] == {"a": [1, 2]}

    async def test_multipart_with_a_file(self):
        router = Router()

        def upload(request):
            f = request.file("doc")
            return JsonResponse({"title": request.input("title"), "name": f.filename, "size": f.size})

        router.post("/upload", upload)
        b = "BOUND"
        body = (
            f'--{b}\r\nContent-Disposition: form-data; name="title"\r\n\r\nhello\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="doc"; filename="a.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\nfile-data\r\n--{b}--\r\n"
        ).encode()
        headers = {"content-type": f"multipart/form-data; boundary={b}"}
        res = await call(
            HttpKernel(router), "POST", "/upload", headers=headers, messages=chunks(body[:40], body[40:90], body[90:])
        )
        assert json.loads(res.body) == {"title": "hello", "name": "a.txt", "size": 9}

    async def test_form_request_binding(self):
        router = Router()

        def save(form: NameForm):
            return JsonResponse(form.validated())

        router.post("/save", save)
        kernel = HttpKernel(router)
        res = await call(kernel, "POST", "/save", headers={**FORM, "accept": "application/json"}, body=b"name=ada")
        assert res.status == 200 and json.loads(res.body) == {"name": "ada"}
        res = await call(kernel, "POST", "/save", headers={**FORM, "accept": "application/json"}, body=b"x=1")
        assert res.status == 422

    async def test_controller_validate(self):
        from hunt.http.controller import Controller

        router = Router()

        class Things(Controller):
            def store(self, request):
                return JsonResponse(self.validate(request, {"name": "required"}))

        router.post("/things", Things().store)
        res = await call(HttpKernel(router), "POST", "/things", headers=JSON, body=b'{"name": "ada"}')
        assert res.status == 200 and json.loads(res.body) == {"name": "ada"}

    async def test_offloaded_sync_handler_reads_input(self, monkeypatch):
        import threading

        monkeypatch.setenv("OFFLOAD_SYNC_HANDLERS", "true")
        main = threading.get_ident()
        router = Router()

        def save(request):
            return JsonResponse({"name": request.input("name"), "offloaded": threading.get_ident() != main})

        router.post("/save", save)
        res = await call(HttpKernel(router), "POST", "/save", headers=FORM, messages=chunks(b"na", b"me=ada"))
        assert res.status == 200
        assert json.loads(res.body) == {"name": "ada", "offloaded": True}

    async def test_error_rendering_sees_an_empty_body_when_it_was_never_read(self):
        seen: list = []

        class Handler:
            def render(self, request, exc):
                seen.append((request.all(), request.body))
                return Response("handled", getattr(exc, "status", 500))

        router = Router()
        router.post("/items", lambda request: Response("ok"))
        kernel = HttpKernel(router, exception_handler=Handler())
        res = await call(kernel, "POST", "/nowhere", headers=FORM, body=b"a=1")
        assert res.status == 404 and res.body == b"handled"
        assert seen == [({}, b"")]
        assert res.receive_calls == 0


# ---------------------------------------------------------------------------
# A real server: uvicorn must cope with responses sent before the body was read
# ---------------------------------------------------------------------------

_SMOKE_APP = """
import hashlib

from hunt.http.kernel import HttpKernel
from hunt.http.response import JsonResponse
from hunt.http.router import Router

router = Router()


def echo(request):
    return JsonResponse({"input": request.all(), "size": len(request.body)})


async def upload(request):
    digest = hashlib.sha256()
    total = 0
    async for chunk in request.stream():
        digest.update(chunk)
        total += len(chunk)
    return JsonResponse({"sha": digest.hexdigest(), "total": total})


router.post("/echo", echo)
router.post("/upload", upload).stream_body().max_body(4 * 1024 * 1024)
app = HttpKernel(router)
"""


def test_real_uvicorn_server(tmp_path):
    import hashlib
    import os
    import socket
    import subprocess
    import sys
    import time

    httpx = pytest.importorskip("httpx")
    pytest.importorskip("uvicorn")

    (tmp_path / "smoke_app.py").write_text(_SMOKE_APP)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, MAX_BODY_SIZE="1000", ACCESS_LOG="false", APP_ENV="testing")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(tmp_path), env.get("PYTHONPATH", "")) if p)
    cmd = [sys.executable, "-m", "uvicorn", "smoke_app:app", "--host", "127.0.0.1", "--port", str(port)]
    proc = subprocess.Popen([*cmd, "--http", "h11", "--log-level", "warning"], cwd=tmp_path, env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                httpx.get(base + "/health", timeout=1)
                break
            except httpx.TransportError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("uvicorn did not start")
                time.sleep(0.05)

        with httpx.Client(base_url=base, timeout=10) as client:
            res = client.post("/echo", data={"a": "1"})
            assert res.status_code == 200 and res.json() == {"input": {"a": "1"}, "size": 3}

            res = client.post("/echo", content=b"x" * 5000)  # Content-Length over MAX_BODY_SIZE
            assert res.status_code == 413 and res.headers["connection"] == "close"

            res = client.post("/echo", content=iter([b"y" * 600, b"y" * 600]))  # chunked, no Content-Length
            assert res.status_code == 413

            res = client.post("/nowhere", content=b"z" * 200_000)  # answered without reading the body
            assert res.status_code == 404

            payload = bytes(range(256)) * 8192  # 2 MiB, over MAX_BODY_SIZE but under the route's limit
            res = client.post("/upload", content=(payload[i : i + 65536] for i in range(0, len(payload), 65536)))
            assert res.status_code == 200
            assert res.json() == {"sha": hashlib.sha256(payload).hexdigest(), "total": len(payload)}

            res = client.post("/echo", json={"still": "serving"})
            assert res.status_code == 200 and res.json()["input"] == {"still": "serving"}
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
