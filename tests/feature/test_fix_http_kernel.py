"""Regression tests for the HTTP kernel findings (HTTP-01..12, HTTP-L7, method override).

Every test drives ``HttpKernel`` through a raw ASGI call.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import ClassVar

import pytest

from hunt.database.model import Model
from hunt.http.kernel import HttpKernel
from hunt.http.middleware import Middleware
from hunt.http.middleware.cors import HandleCors
from hunt.http.middleware.csrf import VerifyCsrfToken
from hunt.http.middleware.secure_headers import SecureHeaders
from hunt.http.middleware.session import COOKIE_NAME, StartSession
from hunt.http.middleware.throttle import ThrottleRequests
from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse, Response, redirect
from hunt.http.router import Router
from hunt.session.store import FileSessionStore
from hunt.validation.form_request import FormRequest


class Cors(HandleCors):
    allowed_origins: ClassVar[list[str]] = ["https://app.example"]


class NameForm(FormRequest):
    def rules(self) -> dict:
        return {"name": "required"}


class Widget(Model):
    table = "widgets"

    @classmethod
    def resolve_route_binding(cls, value):
        if value != "1":
            raise ValueError("not found")
        instance = cls()
        instance.bound_key = value
        return instance


class AsgiResult:
    def __init__(self, sent: list[dict]) -> None:
        self.sent = sent
        start = sent[0] if sent else {"status": None, "headers": []}
        self.status = start.get("status")
        self.raw_headers = [(k.decode().lower(), v.decode()) for k, v in start.get("headers", [])]
        self.headers = dict(self.raw_headers)
        self.body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")

    def all(self, name: str) -> list[str]:
        return [v for k, v in self.raw_headers if k == name]

    def cookie(self, name: str = COOKIE_NAME) -> str | None:
        for value in self.all("set-cookie"):
            if value.startswith(name + "="):
                return value.split(";", 1)[0].split("=", 1)[1]
        return None


async def call(
    kernel: HttpKernel,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    query: str = "",
    messages: list[dict] | None = None,
    client: tuple | None = ("203.0.113.9", 4000),
) -> AsgiResult:
    hdrs = {"host": "app.test", **{k.lower(): v for k, v in (headers or {}).items()}}
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.encode(), v.encode()) for k, v in hdrs.items()],
        "scheme": "http",
        "server": ("0.0.0.0", 8000),
        "client": client,
    }
    queue = list(messages) if messages is not None else [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    await kernel(scope, receive, send)
    return AsgiResult(sent)


FORM = {"content-type": "application/x-www-form-urlencoded"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_LOG", "false")
    monkeypatch.setenv("APP_DEBUG", "false")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("SESSION_SECURE", raising=False)
    sessions = tmp_path / "sessions"
    monkeypatch.setattr("hunt.http.middleware.session._make_session_store", lambda: FileSessionStore(sessions))
    # Static roots are resolved from the working directory; keep it empty.
    monkeypatch.chdir(tmp_path)
    from hunt.http.middleware import throttle

    throttle._hits.clear()
    throttle._expires.clear()
    yield
    throttle._hits.clear()
    throttle._expires.clear()


def _boom(request):
    raise RuntimeError("kaboom")


def _forbidden(request):
    raise HttpException(403, "No entry.")


def _session_form(request):
    return Response(f"token={request.session().csrf_token()} errors={request.session().get_flash('_errors')}")


def _full_kernel() -> HttpKernel:
    router = Router()
    router.get("/form", _session_form)
    router.post("/submit", lambda request, form: Response("saved"))
    router.routes()[-1].action = _submit
    router.get("/forbidden", _forbidden)
    router.get("/boom", _boom)
    router.get("/old", lambda request: Response(f"old={request.old('name')} pw={request.old('password')}"))
    return HttpKernel(router, global_middleware=[SecureHeaders, Cors, StartSession, VerifyCsrfToken])


def _submit(request, form: NameForm):
    return Response("saved")


async def _open_session(kernel: HttpKernel) -> tuple[str, str]:
    """GET the form; return (session cookie, csrf token)."""
    res = await call(kernel, "GET", "/form")
    cookie = res.cookie()
    assert cookie, "a session that holds a CSRF token must be given a cookie"
    token = res.body.decode().split("token=")[1].split(" ")[0]
    return cookie, token


# ---------------------------------------------------------------------------
# HTTP-01: exceptions are converted inside the pipeline
# ---------------------------------------------------------------------------


class TestExceptionsStayInsideThePipeline:
    async def test_validation_errors_and_old_input_survive_the_redirect(self):
        kernel = _full_kernel()
        cookie, token = await _open_session(kernel)

        res = await call(
            kernel,
            "POST",
            "/submit",
            headers={**FORM, "cookie": f"{COOKIE_NAME}={cookie}", "referer": "http://app.test/form?step=2"},
            body=f"_token={token}&name=&other=kept&password=hunter2".encode(),
        )
        assert res.status == 302
        # HTTP-05: the absolute same-origin Referer is reduced to its path.
        assert res.headers["location"] == "/form?step=2"
        assert res.cookie() == cookie

        store = FileSessionStore(Path.cwd() / "sessions")
        store.start(cookie)
        flashed = store.all()["_flash_new"]
        assert "name" in flashed["_errors"]
        assert flashed["_old_input"]["other"] == "kept"
        assert "password" not in flashed["_old_input"]

        follow = await call(kernel, "GET", "/form", headers={"cookie": f"{COOKIE_NAME}={cookie}"})
        assert "name" in follow.body.decode().split("errors=")[1]

    @pytest.mark.parametrize(
        ("method", "path", "status"),
        [("GET", "/forbidden", 403), ("GET", "/missing", 404), ("POST", "/submit", 419), ("GET", "/boom", 500)],
    )
    async def test_error_responses_keep_middleware_headers_and_session(self, method, path, status):
        kernel = _full_kernel()
        cookie, _token = await _open_session(kernel)

        res = await call(
            kernel,
            method,
            path,
            headers={**FORM, "cookie": f"{COOKIE_NAME}={cookie}", "origin": "https://app.example"},
            body=b"name=x",
        )
        assert res.status == status
        assert res.headers["x-frame-options"] == "SAMEORIGIN"
        assert "content-security-policy" in res.headers
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["access-control-allow-origin"] == "https://app.example"
        assert res.headers.get("x-request-id")
        assert res.cookie() == cookie

    async def test_session_changes_survive_an_exception(self):
        def login_then_crash(request):
            request.session().regenerate()
            request.session().put("_auth_id", 7)
            raise HttpException(403, "nope")

        router = Router()
        router.get("/form", _session_form)
        router.get("/crash", login_then_crash)
        kernel = HttpKernel(router, global_middleware=[StartSession])
        cookie, _ = await _open_session(kernel)

        res = await call(kernel, "GET", "/crash", headers={"cookie": f"{COOKIE_NAME}={cookie}"})
        assert res.status == 403
        new_cookie = res.cookie()
        assert new_cookie and new_cookie != cookie
        store = FileSessionStore(Path.cwd() / "sessions")
        store.start(new_cookie)
        assert store.get("_auth_id") == 7

    async def test_405_goes_through_global_middleware_and_sets_allow(self):
        kernel = _full_kernel()
        res = await call(kernel, "DELETE", "/form")
        # CSRF is global here, so an unsafe method without a token is refused first.
        assert res.status == 419
        res = await call(HttpKernel(kernel._router, global_middleware=[SecureHeaders]), "DELETE", "/form")
        assert res.status == 405
        assert res.headers["allow"] == "GET, HEAD, OPTIONS"
        assert res.headers["x-frame-options"] == "SAMEORIGIN"

    async def test_middleware_ordering_is_unchanged(self):
        order: list[str] = []

        def tracer(name):
            class Tracer(Middleware):
                async def handle(self, request, next):
                    order.append(f"{name}:in")
                    response = await next(request)
                    order.append(f"{name}:out")
                    return response

            return Tracer

        router = Router()
        router.get("/x", lambda request: Response("ok")).middleware(tracer("route"))
        res = await call(HttpKernel(router, global_middleware=[tracer("g1"), tracer("g2")]), "GET", "/x")
        assert res.status == 200
        assert order == ["g1:in", "g2:in", "route:in", "route:out", "g2:out", "g1:out"]


# ---------------------------------------------------------------------------
# HTTP-02: throttle keys on the matched route
# ---------------------------------------------------------------------------


class Tight(ThrottleRequests):
    max_attempts = 2
    decay_seconds = 60


class TestThrottleKeying:
    def _kernel(self) -> HttpKernel:
        router = Router()
        router.post("/login", lambda request: Response("ok")).middleware(Tight)
        router.get("/users/{id}", lambda request, id: Response(id)).middleware(Tight)
        return HttpKernel(router, global_middleware=[SecureHeaders])

    async def test_trailing_slash_variants_share_a_bucket(self):
        kernel = self._kernel()
        statuses = [(await call(kernel, "POST", p)).status for p in ("/login", "/login/", "/login//", "/login")]
        assert statuses == [200, 200, 429, 429]

    async def test_route_parameters_share_a_bucket(self):
        kernel = self._kernel()
        statuses = [(await call(kernel, "GET", f"/users/{i}")).status for i in (1, 2, 3)]
        assert statuses == [200, 200, 429]

    async def test_429_has_retry_after_and_outer_headers(self):
        kernel = self._kernel()
        for _ in range(2):
            await call(kernel, "POST", "/login")
        res = await call(kernel, "POST", "/login")
        assert res.status == 429
        assert 1 <= int(res.headers["retry-after"]) <= 60
        assert res.headers["x-ratelimit-remaining"] == "0"
        assert res.headers["x-frame-options"] == "SAMEORIGIN"

    async def test_different_clients_do_not_share_a_bucket(self):
        kernel = self._kernel()
        for _ in range(2):
            await call(kernel, "POST", "/login")
        res = await call(kernel, "POST", "/login", client=("198.51.100.1", 1))
        assert res.status == 200

    async def test_hits_are_pruned_after_the_window(self):
        from hunt.http.middleware import throttle

        kernel = self._kernel()
        await call(kernel, "POST", "/login")
        await call(kernel, "GET", "/users/1")
        assert len(throttle._hits) == 2
        # Age every hit past its window, then let the periodic sweep run.
        for key in list(throttle._hits):
            throttle._hits[key] = [t - 120 for t in throttle._hits[key]]
            throttle._expires[key] -= 120
        throttle._sweep(time.time(), force=True)
        assert throttle._hits == {}
        assert throttle._expires == {}


# ---------------------------------------------------------------------------
# HTTP-03 / HTTP-11: binding
# ---------------------------------------------------------------------------


def _show_widget(request, widget: Widget):
    return JsonResponse({"key": widget.bound_key})


def _needs_service(request, service: UnknownService):  # noqa: F821
    return Response("never")


class TestHandlerBinding:
    def test_exceptions_module_exists(self):
        from hunt.http import exceptions
        from hunt.validation.validator import ValidationException

        assert exceptions.HttpException is HttpException
        assert exceptions.ValidationException is ValidationException

    async def test_route_model_binding_with_string_annotations(self):
        router = Router()
        router.get("/widgets/{widget}", _show_widget)
        kernel = HttpKernel(router)
        ok = await call(kernel, "GET", "/widgets/1")
        assert ok.status == 200 and b'"key": "1"' in ok.body
        missing = await call(kernel, "GET", "/widgets/999")
        assert missing.status == 404

    async def test_form_request_with_string_annotations(self):
        router = Router()
        router.post("/submit", _submit)
        kernel = HttpKernel(router)
        ok = await call(kernel, "POST", "/submit", headers=FORM, body=b"name=Ada")
        assert ok.status == 200 and ok.body == b"saved"
        bad = await call(kernel, "POST", "/submit", headers={**FORM, "accept": "application/json"}, body=b"")
        assert bad.status == 422

    async def test_unresolvable_parameter_is_reported_clearly(self, monkeypatch):
        seen: list[BaseException] = []
        from hunt.log.manager import Log

        monkeypatch.setattr(Log, "exception", lambda *a, **kw: seen.append(kw.get("exc")), raising=False)
        router = Router()
        router.get("/svc", _needs_service)
        res = await call(HttpKernel(router), "GET", "/svc")
        assert res.status == 500
        assert seen and "Cannot bind parameter 'service'" in str(seen[0])


# ---------------------------------------------------------------------------
# HTTP-05: redirect back
# ---------------------------------------------------------------------------


class TestRedirectBack:
    def _kernel(self) -> HttpKernel:
        router = Router()
        router.post("/submit", _submit)
        router.get("/page", lambda request: Response(f"<html>{request.session().csrf_token()}</html>"))
        router.get("/api", lambda request: JsonResponse({"t": request.session().csrf_token()}))
        router.post("/back", lambda request: redirect().back())
        return HttpKernel(router, global_middleware=[StartSession])

    async def test_same_origin_absolute_referer_is_used(self):
        res = await call(
            self._kernel(), "POST", "/submit", headers={**FORM, "referer": "http://app.test/posts/create?x=1"}
        )
        assert (res.status, res.headers["location"]) == (302, "/posts/create?x=1")

    @pytest.mark.parametrize(
        "referer",
        [
            "https://evil.example/form",
            "http://app.test.evil.example/form",
            "http://app.test@evil.example/",
            "//evil.example",
        ],
    )
    async def test_cross_origin_referer_falls_back_to_root(self, referer):
        res = await call(self._kernel(), "POST", "/submit", headers={**FORM, "referer": referer})
        assert (res.status, res.headers["location"]) == (302, "/")

    async def test_previous_url_is_recorded_for_html_page_views_only(self):
        kernel = self._kernel()
        first = await call(kernel, "GET", "/page", query="tab=2")
        cookie = first.cookie()
        hdr = {"cookie": f"{COOKIE_NAME}={cookie}"}
        # A JSON call must not replace the remembered page.
        await call(kernel, "GET", "/api", headers=hdr)
        res = await call(kernel, "POST", "/back", headers=hdr)
        assert (res.status, res.headers["location"]) == (302, "/page?tab=2")

    async def test_back_prefers_same_origin_referer(self):
        res = await call(self._kernel(), "POST", "/back", headers={"referer": "http://app.test/from-here"})
        assert res.headers["location"] == "/from-here"


# ---------------------------------------------------------------------------
# HTTP-07: request body
# ---------------------------------------------------------------------------


class TestRequestBody:
    def _kernel(self, calls: list) -> HttpKernel:
        router = Router()

        def handler(request):
            calls.append(len(request.body))
            return Response("ok")

        router.post("/upload", handler)
        return HttpKernel(router)

    async def test_oversized_body_is_a_clean_413(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "10")
        calls: list = []
        res = await call(self._kernel(calls), "POST", "/upload", body=b"x" * 11)
        assert res.status == 413
        assert calls == []

    async def test_content_length_over_limit_is_refused_before_reading(self, monkeypatch):
        monkeypatch.setenv("MAX_BODY_SIZE", "10")
        calls: list = []
        received: list = []
        kernel = self._kernel(calls)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "query_string": b"",
            "headers": [(b"content-length", b"999")],
        }

        async def receive():
            received.append(1)
            return {"type": "http.request", "body": b"x", "more_body": False}

        sent: list = []

        async def send(message):
            sent.append(message)

        await kernel(scope, receive, send)
        assert sent[0]["status"] == 413
        assert received == [] and calls == []

    async def test_chunked_body_is_joined(self):
        calls: list = []
        messages = [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
        res = await call(self._kernel(calls), "POST", "/upload", messages=messages)
        assert res.status == 200 and calls == [6]

    async def test_disconnect_mid_body_does_not_run_the_handler(self):
        calls: list = []
        messages = [{"type": "http.request", "body": b"abc", "more_body": True}, {"type": "http.disconnect"}]
        res = await call(self._kernel(calls), "POST", "/upload", messages=messages)
        assert calls == []
        assert res.sent == []


# ---------------------------------------------------------------------------
# HTTP-08: static files
# ---------------------------------------------------------------------------


class TestStaticFiles:
    def _kernel(self, tmp_path) -> HttpKernel:
        public = tmp_path / "public"
        uploads = tmp_path / "storage" / "app" / "public"
        public.mkdir()
        uploads.mkdir(parents=True)
        (public / "app.css").write_bytes(b"body{}")
        (public / "index.html").write_bytes(b"<h1>home</h1>")
        (public / "big.txt").write_bytes(b"0123456789" * 20000)
        (uploads / "evil.html").write_bytes(b"<script>alert(1)</script>")
        (uploads / "evil.js").write_bytes(b"alert(1)")
        (uploads / "cat.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        router = Router()
        router.post("/app.css", lambda request: Response("dynamic"))
        return HttpKernel(router)

    async def test_get_serves_with_nosniff(self, tmp_path):
        res = await call(self._kernel(tmp_path), "GET", "/app.css")
        assert res.status == 200 and res.body == b"body{}"
        assert res.headers["content-type"] == "text/css"
        assert res.headers["x-content-type-options"] == "nosniff"

    async def test_head_sends_headers_only(self, tmp_path):
        res = await call(self._kernel(tmp_path), "HEAD", "/app.css")
        assert res.status == 200 and res.body == b""
        assert res.headers["content-length"] == "6"

    async def test_other_methods_are_not_served_from_disk(self, tmp_path):
        kernel = self._kernel(tmp_path)
        assert (await call(kernel, "POST", "/app.css")).body == b"dynamic"
        assert (await call(kernel, "DELETE", "/index.html")).status == 404

    async def test_large_file_is_streamed_in_chunks(self, tmp_path):
        res = await call(self._kernel(tmp_path), "GET", "/big.txt")
        bodies = [m for m in res.sent if m["type"] == "http.response.body"]
        assert len(bodies) > 1
        assert all(len(m["body"]) <= 64 * 1024 for m in bodies)
        assert [m.get("more_body", False) for m in bodies][-1] is False
        assert res.body == b"0123456789" * 20000
        assert res.headers["content-length"] == "200000"

    async def test_range_request(self, tmp_path):
        kernel = self._kernel(tmp_path)
        res = await call(kernel, "GET", "/big.txt", headers={"range": "bytes=5-14"})
        assert res.status == 206
        assert res.body == b"5678901234"
        assert res.headers["content-range"] == "bytes 5-14/200000"
        tail = await call(kernel, "GET", "/big.txt", headers={"range": "bytes=-3"})
        assert (tail.status, tail.body) == (206, b"789")
        bad = await call(kernel, "GET", "/big.txt", headers={"range": "bytes=999999-"})
        assert bad.status == 416 and bad.headers["content-range"] == "bytes */200000"

    @pytest.mark.parametrize("name", ["evil.html", "evil.js"])
    async def test_active_content_under_storage_is_neutralised(self, tmp_path, name):
        res = await call(self._kernel(tmp_path), "GET", f"/storage/{name}")
        assert res.status == 200
        assert res.headers["content-type"] == "application/octet-stream"
        assert res.headers["content-disposition"] == "attachment"
        assert res.headers["content-security-policy"].startswith("sandbox")
        assert res.headers["x-content-type-options"] == "nosniff"

    async def test_passive_uploads_and_public_html_keep_their_type(self, tmp_path):
        kernel = self._kernel(tmp_path)
        image = await call(kernel, "GET", "/storage/cat.png")
        assert image.headers["content-type"] == "image/png"
        assert "content-disposition" not in image.headers
        page = await call(kernel, "GET", "/")
        assert page.headers["content-type"].startswith("text/html")
        assert "content-disposition" not in page.headers


# ---------------------------------------------------------------------------
# HTTP-L7: preflight
# ---------------------------------------------------------------------------


class TestPreflight:
    async def test_route_level_cors_answers_options(self):
        calls: list = []

        class Guard(Middleware):
            async def handle(self, request, next):
                calls.append("guard")
                raise HttpException(401, "login first")

        router = Router()
        router.post("/api/items", lambda request: Response("ok")).middleware(Cors, Guard)
        res = await call(HttpKernel(router), "OPTIONS", "/api/items", headers={"origin": "https://app.example"})
        assert res.status == 204
        assert res.headers["access-control-allow-origin"] == "https://app.example"
        assert "Origin" in res.headers["vary"]
        assert calls == []  # auth is not asked to approve a preflight

    async def test_implicit_options_lists_allowed_methods(self):
        router = Router()
        router.get("/a", lambda request: Response("ok"))
        res = await call(HttpKernel(router), "OPTIONS", "/a")
        assert res.status == 204 and res.headers["allow"] == "GET, HEAD, OPTIONS"
        assert (await call(HttpKernel(router), "OPTIONS", "/nope")).status == 404

    async def test_preflight_errors_are_not_masked_as_204(self):
        class Broken(Middleware):
            async def handle(self, request, next):
                raise RuntimeError("broken middleware")

        router = Router()
        router.get("/a", lambda request: Response("ok"))
        res = await call(HttpKernel(router, global_middleware=[Broken]), "OPTIONS", "/a")
        assert res.status == 500

    async def test_disallowed_origin_still_varies_on_origin(self):
        router = Router()
        router.get("/a", lambda request: Response("ok").header("Vary", "Accept-Language"))
        res = await call(
            HttpKernel(router, global_middleware=[Cors]), "GET", "/a", headers={"origin": "https://x.example"}
        )
        assert "access-control-allow-origin" not in res.headers
        assert res.headers["vary"] == "Accept-Language, Origin"


# ---------------------------------------------------------------------------
# _method override
# ---------------------------------------------------------------------------


class TestMethodOverride:
    def _kernel(self, middleware: list | None = None) -> HttpKernel:
        router = Router()
        router.get("/form", _session_form)
        router.put("/posts/{id}", lambda request, id: Response(f"put {id} via {request.real_method}"))
        router.delete("/posts/{id}", lambda request, id: Response(f"deleted {id}"))
        router.post("/posts/{id}", lambda request, id: Response(f"post {id}"))
        router.get("/posts/{id}", lambda request, id: Response(f"get {id}"))
        return HttpKernel(router, global_middleware=middleware or [])

    async def test_post_form_field_selects_the_method(self):
        kernel = self._kernel()
        put = await call(kernel, "POST", "/posts/5", headers=FORM, body=b"_method=put&title=x")
        assert put.body == b"put 5 via POST"
        deleted = await call(kernel, "POST", "/posts/5", headers=FORM, body=b"_method=DELETE")
        assert deleted.body == b"deleted 5"

    async def test_multipart_form_field_selects_the_method(self):
        body = b'--b\r\nContent-Disposition: form-data; name="_method"\r\n\r\nDELETE\r\n--b--\r\n'
        res = await call(
            self._kernel(), "POST", "/posts/5", headers={"content-type": "multipart/form-data; boundary=b"}, body=body
        )
        assert res.body == b"deleted 5"

    async def test_only_post_and_only_the_form_body_are_honoured(self):
        kernel = self._kernel()
        # Query string on a POST: ignored.
        assert (await call(kernel, "POST", "/posts/5", headers=FORM, query="_method=DELETE")).body == b"post 5"
        # A GET can never be upgraded.
        assert (await call(kernel, "GET", "/posts/5", query="_method=DELETE")).body == b"get 5"
        assert (await call(kernel, "GET", "/posts/5", headers=FORM, body=b"_method=DELETE")).body == b"get 5"
        # JSON bodies and unknown verbs are ignored.
        json_res = await call(
            kernel, "POST", "/posts/5", headers={"content-type": "application/json"}, body=b'{"_method": "DELETE"}'
        )
        assert json_res.body == b"post 5"
        assert (await call(kernel, "POST", "/posts/5", headers=FORM, body=b"_method=GET")).body == b"post 5"

    async def test_csrf_still_applies_to_the_overridden_request(self):
        kernel = self._kernel([StartSession, VerifyCsrfToken])
        cookie, token = await _open_session(kernel)
        hdr = {**FORM, "cookie": f"{COOKIE_NAME}={cookie}"}
        refused = await call(kernel, "POST", "/posts/5", headers=hdr, body=b"_method=DELETE")
        assert refused.status == 419
        allowed = await call(kernel, "POST", "/posts/5", headers=hdr, body=f"_method=DELETE&_token={token}".encode())
        assert allowed.body == b"deleted 5"

    async def test_csrf_token_in_the_query_string_is_not_accepted(self):
        kernel = self._kernel([StartSession, VerifyCsrfToken])
        cookie, token = await _open_session(kernel)
        res = await call(
            kernel, "POST", "/posts/5", headers={**FORM, "cookie": f"{COOKIE_NAME}={cookie}"}, query=f"_token={token}"
        )
        assert res.status == 419


# ---------------------------------------------------------------------------
# request.route, non-HTTP scopes, lifespan
# ---------------------------------------------------------------------------


class TestKernelMisc:
    async def test_request_route_is_set_before_middleware_runs(self):
        seen: list = []

        class Spy(Middleware):
            async def handle(self, request: Request, next):
                seen.append(request.route.uri if request.route is not None else None)
                return await next(request)

        router = Router()
        router.get("/users/{id}", lambda request, id: Response(id))
        kernel = HttpKernel(router, global_middleware=[Spy])
        await call(kernel, "GET", "/users/5/")
        await call(kernel, "GET", "/nowhere")
        assert seen == ["/users/{id}", None]

    async def test_websocket_scope_is_closed_not_left_hanging(self):
        sent: list = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        await HttpKernel(Router())({"type": "websocket", "path": "/ws"}, receive, send)
        assert sent == [{"type": "websocket.close", "code": 1008}]

    async def _lifespan(self, app) -> list[dict]:
        messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent: list[dict] = []

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        await HttpKernel(Router(), app=app)({"type": "lifespan"}, receive, send)
        return sent

    async def test_failed_startup_hook_reports_startup_failed(self):
        class App:
            _startup_handlers: ClassVar[list] = [lambda: (_ for _ in ()).throw(RuntimeError("db down"))]
            _shutdown_handlers: ClassVar[list] = []

        sent = await self._lifespan(App())
        assert [m["type"] for m in sent] == ["lifespan.startup.failed"]
        assert "db down" in sent[0]["message"]

    async def test_successful_lifespan_completes(self):
        ran: list[str] = []

        class App:
            _startup_handlers: ClassVar[list] = [lambda: ran.append("up")]
            _shutdown_handlers: ClassVar[list] = [lambda: ran.append("down")]

        sent = await self._lifespan(App())
        assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert ran == ["up", "down"]
