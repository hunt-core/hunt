"""Unit-level regression tests for the HTTP, session and Redis findings."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response, _is_safe_redirect, _same_origin_path
from hunt.http.router import Router


def make_request(
    method: str = "GET",
    path: str = "/",
    *,
    headers: dict[str, str] | None = None,
    raw_headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
    query: bytes = b"",
    **scope,
) -> Request:
    hdrs = (
        raw_headers
        if raw_headers is not None
        else [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    )
    return Request(
        {"type": "http", "method": method, "path": path, "query_string": query, "headers": hdrs, **scope}, body
    )


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# HTTP-04 / HTTP-12: routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_named_registers_without_manual_call(self):
        r = Router()
        r.get("/a/{id}", lambda: None).named("a")
        assert r.url("a", {"id": 3}) == "/a/3"

    def test_duplicate_name_raises_and_same_route_is_idempotent(self):
        r = Router()
        route = r.get("/a", lambda: None).named("a")
        r._register_named(route)  # the old manual call stays harmless
        with pytest.raises(ValueError, match="already registered"):
            r.get("/b", lambda: None).named("a")
        assert r.routes()[-1].name is None

    def test_renaming_releases_the_old_name(self):
        r = Router()
        route = r.get("/a", lambda: None).named("old")
        route.named("new")
        assert r.url("new") == "/a"
        with pytest.raises(KeyError):
            r.url("old")

    def test_resource_routes_are_named(self):
        r = Router()
        r.resource("posts", MagicMock())
        assert r.url("posts.show", {"id": 9}) == "/posts/9"
        assert r.url("posts.index") == "/posts"

    def test_optional_parameter_matches_with_and_without(self):
        r = Router()
        r.get("/users/{id?}", lambda: None).named("u")
        assert r.dispatch("GET", "/users")[1] == {"id": None}
        assert r.dispatch("GET", "/users/5")[1] == {"id": "5"}
        assert r.url("u") == "/users"
        assert r.url("u", {"id": 5}) == "/users/5"

    def test_literal_text_is_escaped(self):
        from hunt.http.router import RouteNotFoundException

        r = Router()
        r.get("/file.json", lambda: None)
        r.dispatch("GET", "/file.json")
        with pytest.raises(RouteNotFoundException):
            r.dispatch("GET", "/fileXjson")

    def test_constraints_still_work(self):
        from hunt.http.router import RouteNotFoundException

        r = Router()
        r.get(r"/p/{id:\d+}", lambda: None)
        r.get("/f/{path:.+}", lambda: None).named("f")
        assert r.dispatch("GET", "/p/12")[1] == {"id": "12"}
        with pytest.raises(RouteNotFoundException):
            r.dispatch("GET", "/p/ab")
        assert r.dispatch("GET", "/f/a/b")[1] == {"path": "a/b"}
        assert r.url("f", {"path": "a/b c"}) == "/f/a/b%20c"

    def test_url_encodes_values_and_survives_backslashes(self):
        r = Router()
        r.get("/users/{id}", lambda: None).named("u")
        assert r.url("u", {"id": r"a\1"}) == "/users/a%5C1"
        assert r.url("u", {"id": "../admin?x=1#f"}) == "/users/..%2Fadmin%3Fx%3D1%23f"

    def test_url_raises_on_missing_required_parameter(self):
        r = Router()
        r.get("/users/{id}", lambda: None).named("u")
        with pytest.raises(ValueError, match="Missing required parameter 'id'"):
            r.url("u")


# ---------------------------------------------------------------------------
# HTTP-L1 / L2 / L3 / L8: request
# ---------------------------------------------------------------------------


JSON = {"content-type": "application/json"}


class TestRequestInput:
    @pytest.mark.parametrize("body", [b"[]", b'"text"', b"42", b"null", b"[1, 2]"])
    def test_non_object_json_is_empty_input(self, body):
        req = make_request("POST", headers=JSON, body=body)
        assert req.input("_token") is None
        assert req.all() == {}
        assert req.has("x") is False

    def test_non_object_json_is_still_available_raw(self):
        assert make_request("POST", headers=JSON, body=b"[1, 2]").json() == [1, 2]

    def test_deeply_nested_json_does_not_raise(self):
        req = make_request("POST", headers=JSON, body=b"[" * 200000 + b"]" * 200000)
        assert req.all() == {}

    def test_merge_on_non_object_json(self):
        req = make_request("POST", headers=JSON, body=b"[]")
        req.merge({"a": 1})
        assert req.all() == {"a": 1}

    def test_non_utf8_query_and_headers_do_not_raise(self):
        req = make_request(query=b"q=%ff&r=\xff", raw_headers=[(b"x-test", b"caf\xe9"), (b"X-Other", b"ok")])
        assert req.query("q") == "�"
        assert isinstance(req.query("r"), str)
        assert req.headers() == {"x-test": "caf�", "x-other": "ok"}

    def test_query_parameters_are_merged_under_body_input(self):
        form = {"content-type": "application/x-www-form-urlencoded"}
        req = make_request("POST", headers=form, body=b"name=body&only_body=1", query=b"name=query&page=2")
        assert req.input("page") == "2"
        assert req.input("name") == "body"
        assert req.all() == {"name": "body", "page": "2", "only_body": "1"}

    def test_get_parameters_reach_all_and_validation(self):
        from hunt.validation.validator import Validator

        req = make_request(query=b"page=3&tag=a&tag=b")
        assert req.all() == {"page": "3", "tag": ["a", "b"]}
        assert Validator(req.all(), {"page": "required|integer"}).fails() is False

    def test_path_params_still_win(self):
        req = make_request(query=b"id=query")
        req.set_route_params({"id": "path"})
        assert req.input("id") == "path" and req.all()["id"] == "path"

    def test_query_accessors_are_unchanged(self):
        req = make_request(query=b"a=1&a=2")
        assert req.query("a") == "1" and req.query_all("a") == ["1", "2"] and req.query("z", "d") == "d"


def _multipart(*parts: bytes, boundary: bytes = b"XB") -> bytes:
    return b"".join(b"--" + boundary + b"\r\n" + p + b"\r\n" for p in parts) + b"--" + boundary + b"--\r\n"


class TestMultipart:
    def _req(self, body: bytes, content_type: str = "multipart/form-data; boundary=XB") -> Request:
        return make_request("POST", headers={"content-type": content_type}, body=body)

    def test_quoted_semicolon_cannot_smuggle_a_field_name(self):
        body = _multipart(
            b'Content-Disposition: form-data; name="real"; filename="x; name=is_admin.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\ndata"
        )
        req = self._req(body)
        assert req.file("real").filename == "x; name=is_admin.txt"
        assert req.file("is_admin") is None and req.input("is_admin") is None

    @pytest.mark.parametrize("name", ["..", ".", "", "../..", "/"])
    def test_dot_filenames_are_replaced(self, name):
        body = _multipart(f'Content-Disposition: form-data; name="f"; filename="{name}"\r\n\r\ndata'.encode())
        assert self._req(body).file("f").filename == "upload"

    def test_path_components_are_stripped(self):
        body = _multipart(b'Content-Disposition: form-data; name="f"; filename="..\\\\..\\\\etc/passwd"\r\n\r\ndata')
        assert self._req(body).file("f").filename == "passwd"

    def test_boundary_parameter_is_case_insensitive_and_may_be_quoted(self):
        body = _multipart(b'Content-Disposition: form-data; name="a"\r\n\r\n1')
        assert self._req(body, 'multipart/form-data; Boundary="XB"').input("a") == "1"

    def test_repeated_fields_and_files_are_collected(self):
        body = _multipart(
            b'Content-Disposition: form-data; name="tags"\r\n\r\none',
            b'Content-Disposition: form-data; name="tags"\r\n\r\ntwo',
            b'Content-Disposition: form-data; name="docs"; filename="a.txt"\r\n\r\nA',
            b'Content-Disposition: form-data; name="docs"; filename="b.txt"\r\n\r\nB',
        )
        req = self._req(body)
        assert req.input("tags") == ["one", "two"]
        assert [f.filename for f in req.files("docs")] == ["a.txt", "b.txt"]
        assert req.file("docs").filename == "b.txt"

    def test_delimiter_is_anchored_to_a_line_start(self):
        body = _multipart(b'Content-Disposition: form-data; name="note"\r\n\r\ntext --XB still text --XBextra')
        assert self._req(body).input("note") == "text --XB still text --XBextra"

    def test_non_utf8_filename_does_not_raise(self):
        body = _multipart(b'Content-Disposition: form-data; name="f"; filename="caf\xe9.txt"\r\n\r\ndata')
        assert self._req(body).file("f").filename.endswith(".txt")


class TestRequestMisc:
    def test_host_parsing(self):
        assert make_request(headers={"host": "Example.com:8000"}).host == "example.com"
        assert make_request(headers={"host": "[::1]:8000"}).host == "[::1]"
        assert make_request(headers={"host": "[2001:db8::1]"}).host == "[2001:db8::1]"
        assert make_request(headers={"host": "example.com"}).host == "example.com"

    def test_full_url_uses_the_host_header_not_the_bind_address(self):
        req = make_request(path="/x", headers={"host": "shop.example"}, scheme="https", server=("0.0.0.0", 8000))
        assert req.full_url == "https://shop.example/x"
        fallback = make_request(path="/x", scheme="http", server=("::1", 8000))
        assert fallback.full_url == "http://[::1]:8000/x"
        assert make_request(path="/x", scheme="http", server=("localhost", 80)).full_url == "http://localhost/x"

    def test_flash_drops_passwords_and_uploads(self):
        form = {"content-type": "application/x-www-form-urlencoded"}
        req = make_request("POST", headers=form, body=b"email=a%40b.c&password=s3cret&password_confirmation=s3cret")
        store = MagicMock()
        req._session = store
        req.flash()
        store.flash.assert_called_once_with("_old_input", {"email": "a@b.c"})
        store.reset_mock()
        req.flash_only("email", "password")
        store.flash.assert_called_once_with("_old_input", {"email": "a@b.c"})

    def test_ip_with_missing_client(self):
        assert make_request(client=None).ip == "127.0.0.1"
        assert make_request(client=(None, 0)).ip == "127.0.0.1"


# ---------------------------------------------------------------------------
# HTTP-L4 / L5 / HTTP-05: response
# ---------------------------------------------------------------------------


def header_values(resp: Response, name: bytes) -> list[bytes]:
    return [v for k, v in resp._asgi_headers() if k == name]


class TestResponseHeaders:
    def test_cookie_value_cannot_inject_attributes(self):
        resp = Response("ok").with_cookie("pref", "x; Domain=evil.example, a=b\r\nSet-Cookie: c=d")
        (cookie,) = header_values(resp, b"set-cookie")
        value = cookie.split(b"; ")[0]
        assert value == b"pref=x%3B%20Domain=evil.example%2C%20a=b%0D%0ASet-Cookie:%20c=d"
        assert b"\r" not in cookie and b"\n" not in cookie
        assert cookie.count(b";") == 3  # Path, SameSite, HttpOnly only

    def test_cookie_round_trips_through_request(self):
        original = 'a b;c,"d" 100% é'
        (cookie,) = header_values(Response("ok").with_cookie("k", original), b"set-cookie")
        sent_back = cookie.split(b";")[0].decode()
        assert make_request(headers={"cookie": sent_back}).cookie("k") == original

    def test_cookie_name_and_attributes_are_sanitised(self):
        resp = Response("ok").with_cookie("na;me=", "v", path="/x; Secure", same_site="Lax\r\nX: y")
        (cookie,) = header_values(resp, b"set-cookie")
        assert cookie.startswith(b"name=v; Path=/x Secure; SameSite=LaxX: y")

    def test_crlf_is_stripped_from_header_names_and_values(self):
        resp = Response("ok", headers={"X-A": "1\r\nX-Injected: 2"})
        resp.header("X-B\r\n", "v\nw")
        resp._headers["X-Direct"] = "a\r\nb"
        resp.add_cookie_header("a=b\r\nSet-Cookie: c=d")
        for name, value in resp._asgi_headers():
            assert b"\r" not in name + value and b"\n" not in name + value
        assert header_values(resp, b"x-a") == [b"1X-Injected: 2"]

    def test_redirect_location_cannot_split_the_response(self):
        resp = RedirectResponse("/next\r\nSet-Cookie: a=b")
        assert header_values(resp, b"location") == [b"/nextSet-Cookie: a=b"]

    def test_content_type_via_header_is_not_duplicated(self):
        resp = Response("{}").header("Content-Type", "application/json")
        assert header_values(resp, b"content-type") == [b"application/json"]
        assert resp.content_type == "application/json"
        assert Response("x", headers={"content-type": "text/plain"}).content_type == "text/plain"

    def test_header_names_are_case_insensitive(self):
        resp = Response("ok").header("X-Thing", "1").header("x-thing", "2")
        assert header_values(resp, b"x-thing") == [b"2"]
        assert resp.get_header("X-THING") == "2"

    def test_vary_appends(self):
        resp = Response("ok").header("Vary", "Accept-Encoding").vary("Origin").vary("origin", "Cookie")
        assert resp.get_header("Vary") == "Accept-Encoding, Origin, Cookie"

    def test_headers_dict_is_copied(self):
        shared = {"X-A": "1"}
        first = Response("a", headers=shared)
        first.header("X-B", "2")
        assert shared == {"X-A": "1"}
        assert Response("b", headers=shared).get_header("X-B") is None

    def test_http_exception_carries_headers(self):
        exc = HttpException(429, "slow down", headers={"Retry-After": "7"})
        assert exc.headers == {"Retry-After": "7"}
        assert HttpException(404).headers == {}


class TestSafeRedirect:
    @pytest.mark.parametrize("url", ["/", "/dashboard", "/a/b?x=1&y=2#frag", "/search?q=http://x"])
    def test_local_paths_are_safe(self, url):
        assert _is_safe_redirect(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "//evil.com",
            "///evil.com",
            "/\\evil.com",
            "\\/evil.com",
            "/\t\\evil.com",
            "/\t/evil.com",
            "/\n/evil.com",
            "/a b",
            "/a\\b",
            "/\x00",
            "https://evil.com",
            "javascript:alert(1)",
            "evil.com",
            "relative/path",
            " /x",
        ],
    )
    def test_everything_else_is_rejected(self, url):
        assert _is_safe_redirect(url) is False

    def test_same_origin_path(self):
        req = make_request(headers={"host": "App.Test:8000"})
        assert _same_origin_path("http://app.test:8000/form?a=1", req) == "/form?a=1"
        assert _same_origin_path("https://app.test:8000", req) == "/"
        assert _same_origin_path("/already/local", req) == "/already/local"
        for bad in (
            "http://app.test/form",  # different port
            "http://evil.test:8000/form",
            "http://app.test:8000@evil.test/",
            "http://app.test:8000//evil.test",
            "ftp://app.test:8000/x",
            "http://app.test:8000/a\\b",
            None,
            "",
        ):
            assert _same_origin_path(bad, req) is None


# ---------------------------------------------------------------------------
# HTTP-10: TrustProxies
# ---------------------------------------------------------------------------


async def _passthrough(request):
    return Response("ok")


class TestTrustProxies:
    def _run(self, request, cls=None, **env):
        from hunt.http.middleware.trust_proxies import TrustProxies

        with patch.dict("os.environ", env, clear=False):
            run((cls or TrustProxies)().handle(request, _passthrough))
        return request

    def test_client_none_does_not_crash_and_is_untrusted_by_default(self):
        req = make_request(headers={"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https"}, client=None)
        self._run(req, TRUSTED_PROXIES="10.0.0.0/8")
        assert req._scope["client"] is None
        assert req.ip == "127.0.0.1"
        assert req._scope.get("scheme") is None

    def test_unix_socket_peer_can_be_trusted_explicitly(self):
        req = make_request(headers={"x-forwarded-for": "198.51.100.7", "x-forwarded-proto": "https"}, client=None)
        self._run(req, TRUSTED_PROXIES="10.0.0.0/8", TRUSTED_PROXIES_UNIX_SOCKET="true")
        assert req.ip == "198.51.100.7"
        assert req._scope["scheme"] == "https"

    def test_garbage_forwarded_for_is_ignored(self):
        req = make_request(headers={"x-forwarded-for": "<script>, not-an-ip"}, client=("10.0.0.1", 5))
        self._run(req, TRUSTED_PROXIES="10.0.0.0/8")
        assert req.ip == "10.0.0.1"

    def test_invalid_entries_are_skipped(self):
        req = make_request(headers={"x-forwarded-for": "junk, 198.51.100.7, 10.0.0.2"}, client=("10.0.0.1", 5))
        self._run(req, TRUSTED_PROXIES="10.0.0.0/8")
        assert req.ip == "198.51.100.7"

    def test_multi_value_proto_uses_the_first(self):
        req = make_request(headers={"x-forwarded-proto": "HTTPS, http"}, client=("10.0.0.1", 5), scheme="http")
        self._run(req, TRUSTED_PROXIES="10.0.0.0/8")
        assert req._scope["scheme"] == "https"

    def test_wildcard_uses_the_rightmost_entry(self):
        req = make_request(headers={"x-forwarded-for": "1.1.1.1, 198.51.100.7"}, client=("203.0.113.1", 5))
        self._run(req, TRUSTED_PROXIES="*")
        assert req.ip == "198.51.100.7"

    def test_wildcard_with_hop_count(self):
        req = make_request(headers={"x-forwarded-for": "6.6.6.6, 198.51.100.7, 10.0.0.9"}, client=("203.0.113.1", 5))
        self._run(req, TRUSTED_PROXIES="*", TRUSTED_PROXY_HOPS="2")
        assert req.ip == "198.51.100.7"

    def test_untrusted_peer_is_left_alone(self):
        req = make_request(headers={"x-forwarded-for": "198.51.100.7"}, client=("203.0.113.1", 5))
        self._run(req, TRUSTED_PROXIES="10.0.0.0/8")
        assert req.ip == "203.0.113.1"


# ---------------------------------------------------------------------------
# HTTP-L8 and GEN-09 middleware
# ---------------------------------------------------------------------------


class TestSmallMiddlewareFixes:
    def test_request_id_is_capped_and_charset_checked(self):
        from hunt.http.middleware.request_id import RequestId

        def rid(value: str) -> str:
            req = make_request(headers={"x-request-id": value})
            return run(RequestId().handle(req, _passthrough)).get_header("X-Request-ID")

        assert rid("abc-123_DEF.4:5") == "abc-123_DEF.4:5"
        assert rid("a" * 128) == "a" * 128
        for bad in ("a" * 129, "has space", "<script>", "a\tb"):
            assert rid(bad) != bad
            assert len(rid(bad)) == 36

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("gzip", True),
            ("gzip, deflate, br", True),
            ("gzip;q=0", False),
            ("gzip; q=0.0, br", False),
            ("GZIP;Q=0.5", True),
            ("*", True),
            ("*;q=0", False),
            ("gzip;q=0, *", False),
            ("br, deflate", False),
            ("", False),
            ("gzip;q=oops", False),
        ],
    )
    def test_gzip_quality_zero_is_a_refusal(self, header, expected):
        from hunt.http.middleware.compress import CompressResponse

        assert CompressResponse._client_accepts_gzip(make_request(headers={"accept-encoding": header})) is expected

    def test_bad_hsts_integer_does_not_break_responses(self):
        from hunt.http.middleware.secure_headers import SecureHeaders

        with patch.dict("os.environ", {"SECURE_HSTS_SECONDS": "one-year"}):
            resp = run(SecureHeaders().handle(make_request(), _passthrough))
        assert resp.get_header("Strict-Transport-Security") == "max-age=31536000"

    def test_secure_headers_respect_an_existing_csp_in_any_case(self):
        from hunt.http.middleware.secure_headers import SecureHeaders

        async def with_csp(request):
            return Response("ok").header("content-security-policy", "default-src 'none'")

        resp = run(SecureHeaders().handle(make_request(), with_csp))
        assert resp.get_header("Content-Security-Policy") == "default-src 'none'"

    def test_merge_when_is_lazy(self):
        from hunt.http.resources import ApiResource

        calls: list[int] = []

        def expensive():
            calls.append(1)
            return {"secret": 1}

        res = ApiResource(object())
        assert res.merge_when(False, expensive) == {}
        assert calls == []
        assert res.merge_when(True, expensive) == {"secret": 1}
        assert res.merge_when(True, {"a": 1}) == {"a": 1}

    def test_app_env_has_one_default(self, monkeypatch):
        from hunt.application import DEFAULT_APP_ENV, Application, app_env

        monkeypatch.delenv("APP_ENV", raising=False)
        assert app_env() == DEFAULT_APP_ENV == "production"
        assert Application.is_production(MagicMock()) is True
        assert Application.is_local(MagicMock()) is False
        monkeypatch.setenv("APP_ENV", "local")
        assert app_env() == "local"
        assert Application.is_local(MagicMock()) is True


class TestMaintenanceMode:
    def _handle(self, tmp_path, content: str | None, request: Request | None = None):
        from hunt.http.middleware import maintenance

        sentinel = tmp_path / ".maintenance"
        if content is not None:
            sentinel.write_text(content)
        with patch.object(maintenance, "maintenance_file", return_value=sentinel):
            return run(maintenance.MaintenanceMode().handle(request or make_request(), _passthrough))

    def test_passes_through_when_up(self, tmp_path):
        assert self._handle(tmp_path, None).status == 200

    def test_message_is_html_escaped(self, tmp_path):
        resp = self._handle(tmp_path, json.dumps({"message": "<script>alert(1)</script>", "retry_after": 120}))
        assert resp.status == 503
        assert b"<script>alert(1)</script>" not in resp._body
        assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in resp._body
        assert resp.get_header("Retry-After") == "120"
        assert [v for k, v in resp._asgi_headers() if k == b"content-type"] == [b"text/html; charset=utf-8"]

    @pytest.mark.parametrize(
        "content", ["", "[]", '"down"', "42", "null", "{not json", '{"message": 5, "retry_after": "x"}']
    )
    def test_non_object_json_still_means_down(self, tmp_path, content):
        resp = self._handle(tmp_path, content)
        assert resp.status == 503
        assert b"back shortly" in resp._body
        assert resp.get_header("Retry-After") == "60"

    def test_sentinel_is_resolved_from_the_application_base_path(self, tmp_path, monkeypatch):
        from hunt.container import facade
        from hunt.http.middleware.maintenance import maintenance_file

        elsewhere = tmp_path / "cwd"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setattr(facade, "_app", types.SimpleNamespace(base_path=tmp_path / "project"))
        assert maintenance_file() == tmp_path / "project" / ".maintenance"
        monkeypatch.setattr(facade, "_app", None)
        assert maintenance_file() == elsewhere / ".maintenance"


# ---------------------------------------------------------------------------
# HTTP client (HTTP-L8)
# ---------------------------------------------------------------------------


class TestAsyncHttpClient:
    async def test_async_verbs_use_fakes_and_record(self):
        from hunt.http.client import Http

        Http.fake({"https://api.test/*": Http.response({"id": 1}, 201)})
        try:
            resp = await Http.with_token("t").async_post("https://api.test/users", {"name": "Ada"})
            assert resp.status_code == 201 and resp.json("id") == 1
            assert (await Http.async_get("https://api.test/users/1")).ok()
            Http.assert_sent("https://api.test/*", times=2)
            assert Http.recorded()[0]["method"] == "POST"
        finally:
            Http.unfake()

    async def test_async_send_uses_async_client_and_async_sleep(self):
        from hunt.http import client as client_module

        attempts: list[str] = []
        sleeps: list[float] = []

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def request(self, method, url, **kw):
                attempts.append(method)
                if len(attempts) < 3:
                    raise ConnectionError("down")
                return client_module._httpx.Response(200, content=b"ok")

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        def no_blocking_sleep(_seconds):
            raise AssertionError("time.sleep must not be used on the async path")

        with (
            patch.object(client_module._httpx, "AsyncClient", FakeAsyncClient),
            patch.object(client_module.asyncio, "sleep", fake_sleep),
            patch.object(client_module.time, "sleep", no_blocking_sleep),
        ):
            resp = await client_module.Http.retry(2, sleep_ms=50).async_get("https://api.test/x")
        assert resp.text() == "ok"
        assert attempts == ["GET", "GET", "GET"]
        assert sleeps == [0.05, 0.05]

    def test_sync_verbs_are_still_there(self):
        from hunt.http.client import Http

        Http.fake()
        try:
            assert Http.get("https://api.test/x").ok()
        finally:
            Http.unfake()


# ---------------------------------------------------------------------------
# HTTP-09 / REDIS-L1: Redis connection
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis_module(monkeypatch):
    """A stand-in ``redis`` package that records how the client was built."""
    from hunt import redis_connection

    created: list[tuple[str, tuple, dict]] = []

    class FakeRedis:
        def __init__(self, **kwargs):
            created.append(("init", (), kwargs))

        @classmethod
        def from_url(cls, url, **kwargs):
            created.append(("from_url", (url,), kwargs))
            return object.__new__(cls)

    module = types.ModuleType("redis")
    module.Redis = FakeRedis
    monkeypatch.setitem(sys.modules, "redis", module)
    for name in (
        "REDIS_URL",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_DB",
        "REDIS_PASSWORD",
        "REDIS_USERNAME",
        "REDIS_SSL",
        "REDIS_SOCKET_TIMEOUT",
        "REDIS_SOCKET_CONNECT_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    redis_connection.reset_redis()
    yield created
    redis_connection.reset_redis()


class TestRedisConnection:
    def test_redis_url_is_honoured_with_timeouts(self, fake_redis_module, monkeypatch):
        from hunt.redis_connection import get_redis

        monkeypatch.setenv("REDIS_URL", "rediss://user:pw@cache.internal:6380/2")
        monkeypatch.setenv("REDIS_HOST", "ignored")
        client = get_redis()
        assert get_redis() is client
        ((kind, args, kwargs),) = fake_redis_module
        assert kind == "from_url" and args == ("rediss://user:pw@cache.internal:6380/2",)
        assert kwargs["socket_timeout"] >= 5 and kwargs["socket_connect_timeout"] >= 5
        assert kwargs["health_check_interval"] == 30
        assert kwargs["decode_responses"] is True
        assert "host" not in kwargs

    def test_discrete_settings_with_tls_username_and_timeouts(self, fake_redis_module, monkeypatch):
        from hunt.redis_connection import get_redis

        monkeypatch.setenv("REDIS_HOST", "cache")
        monkeypatch.setenv("REDIS_PORT", "not-a-number")
        monkeypatch.setenv("REDIS_USERNAME", "app")
        monkeypatch.setenv("REDIS_PASSWORD", "pw")
        monkeypatch.setenv("REDIS_SSL", "true")
        monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "0.1")  # below the floor
        get_redis()
        ((kind, _args, kwargs),) = fake_redis_module
        assert kind == "init"
        assert (kwargs["host"], kwargs["port"], kwargs["db"]) == ("cache", 6379, 0)
        assert (kwargs["username"], kwargs["password"], kwargs["ssl"]) == ("app", "pw", True)
        assert kwargs["socket_timeout"] == 5.0


# ---------------------------------------------------------------------------
# HTTP-06 / HTTP-09 / HTTP-L6: session stores
# ---------------------------------------------------------------------------


SID = "ab" * 32


def _stored(session_id: str) -> str:
    """What a session is stored under: sha256 of the cookie value, never the value itself."""
    return hashlib.sha256(session_id.encode()).hexdigest()


class TestFileSessionStore:
    def _store(self, tmp_path):
        from hunt.session.store import FileSessionStore

        return FileSessionStore(tmp_path / "sessions")

    def test_unknown_client_supplied_id_is_not_adopted(self, tmp_path):
        store = self._store(tmp_path)
        store.start(SID)
        assert store.id != SID and len(store.id) == 64
        assert store.existed is False

    def test_known_id_is_adopted(self, tmp_path):
        first = self._store(tmp_path)
        first.start(SID)
        first.put("k", "v")
        assert first.save() is True
        again = self._store(tmp_path)
        again.start(first.id)
        assert again.id == first.id and again.existed is True and again.get("k") == "v"

    def test_save_after_revoke_does_not_resurrect(self, tmp_path):
        first = self._store(tmp_path)
        first.start(SID)
        first.put("_auth_id", 7)
        first.save()

        in_flight = self._store(tmp_path)
        in_flight.start(first.id)
        assert in_flight.get("_auth_id") == 7
        # Revoked while the slow request is still running.
        (tmp_path / "sessions" / _stored(first.id)).unlink()
        assert in_flight.save() is False
        assert not (tmp_path / "sessions" / _stored(first.id)).exists()
        assert not (tmp_path / "sessions" / first.id).exists()

    def test_regenerated_session_is_saved_under_the_new_id(self, tmp_path):
        first = self._store(tmp_path)
        first.start(SID)
        first.put("a", 1)
        first.save()
        again = self._store(tmp_path)
        again.start(first.id)
        new_id = again.regenerate()
        assert again.save() is True
        assert (tmp_path / "sessions" / _stored(new_id)).exists()
        assert not (tmp_path / "sessions" / _stored(first.id)).exists()

    def test_corrupt_or_expired_file_starts_fresh(self, tmp_path):
        store = self._store(tmp_path)
        (tmp_path / "sessions" / SID).write_text("[1, 2")
        store.start(SID)
        assert store.id != SID and store.all() == {}
        (tmp_path / "sessions" / SID).write_text(json.dumps({"data": {"a": 1}, "expires_at": 1}))
        store.start(SID)
        assert store.id != SID and store.get("a") is None


class FakeRedisClient:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.fail_reads = False
        self.fail_writes = False

    def get(self, key):
        if self.fail_reads:
            raise TimeoutError("redis read timed out")
        return self.data.get(key)

    def set(self, key, value, ex=None, xx=False):
        if self.fail_writes:
            raise TimeoutError("redis write timed out")
        if xx and key not in self.data:
            return None
        self.data[key] = value
        return True

    def setex(self, key, ttl, value):
        if self.fail_writes:
            raise TimeoutError("redis write timed out")
        self.data[key] = value
        return True

    def delete(self, key):
        self.data.pop(key, None)


class TestRedisSessionStore:
    def _store(self, client):
        from hunt.session.redis_store import RedisSessionStore

        store = RedisSessionStore()
        store._redis = lambda: client
        return store

    def test_read_error_does_not_overwrite_existing_data(self):
        client = FakeRedisClient()
        key = f"hunt:session:sha256:{_stored(SID)}"
        client.data[key] = json.dumps({"_auth_id": 7})

        store = self._store(client)
        client.fail_reads = True
        store.start(SID)
        client.fail_reads = False
        assert store.read_failed is True
        assert store.id == SID  # the client's cookie stays valid
        store.put("junk", 1)
        assert store.save() is False
        assert json.loads(client.data[key]) == {"_auth_id": 7}

    def test_missing_key_is_a_new_session_not_an_error(self):
        client = FakeRedisClient()
        store = self._store(client)
        store.start(SID)
        assert store.read_failed is False and store.existed is False and store.id != SID
        store.put("a", 1)
        assert store.save() is True
        assert json.loads(client.data[f"hunt:session:sha256:{_stored(store.id)}"]) == {"a": 1}
        assert not any(store.id in key for key in client.data)

    def test_save_after_revoke_does_not_resurrect(self):
        client = FakeRedisClient()
        key = f"hunt:session:sha256:{_stored(SID)}"
        client.data[key] = json.dumps({"_auth_id": 7})
        store = self._store(client)
        store.start(SID)
        assert store.existed is True and store.get("_auth_id") == 7
        client.delete(key)  # revoked mid-request
        assert store.save() is False
        assert key not in client.data

    def test_save_failure_is_logged_not_raised(self, caplog):
        client = FakeRedisClient()
        store = self._store(client)
        store.start(SID)
        store.put("a", 1)
        client.fail_writes = True
        with caplog.at_level("ERROR", logger="hunt.session"):
            assert store.save() is False
        assert "Session save failed" in caplog.text


# ---------------------------------------------------------------------------
# HTTP-L6 / HTTP-09: StartSession
# ---------------------------------------------------------------------------


class TestStartSession:
    @pytest.fixture(autouse=True)
    def _stores(self, tmp_path, monkeypatch):
        from hunt.session.store import FileSessionStore

        self.dir = tmp_path / "sessions"
        monkeypatch.setattr("hunt.http.middleware.session._make_session_store", lambda: FileSessionStore(self.dir))
        monkeypatch.delenv("SESSION_SECURE", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)

    def _run(self, handler, request=None):
        from hunt.http.middleware.session import StartSession

        return run(StartSession().handle(request or make_request(scheme="http"), handler))

    def _cookies(self, resp: Response) -> list[str]:
        return [c for c in resp._cookie_headers if c.startswith("hunt_session=")]

    def _existing(self) -> str:
        async def write(request):
            request.session().put("k", "v")
            return Response("ok")

        return self._cookies(self._run(write))[0].split(";")[0].split("=")[1]

    def test_untouched_session_is_not_created(self):
        resp = self._run(_passthrough)
        assert self._cookies(resp) == []
        assert not self.dir.exists() or list(self.dir.iterdir()) == []

    def test_session_with_data_is_created_and_cookie_set(self):
        sid = self._existing()
        assert (self.dir / _stored(sid)).exists()
        assert not (self.dir / sid).exists()

    def test_secure_defaults_on_in_production_and_can_be_disabled(self, monkeypatch):
        async def write(request):
            request.session().put("k", "v")
            return Response("ok")

        assert "; Secure" in self._cookies(self._run(write))[0]  # APP_ENV unset => production
        monkeypatch.setenv("APP_ENV", "local")
        assert "; Secure" not in self._cookies(self._run(write))[0]
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("SESSION_SECURE", "false")
        assert "; Secure" not in self._cookies(self._run(write))[0]

    def test_public_cacheable_response_gets_no_cookie(self):
        sid = self._existing()

        async def cached(request):
            return Response("asset").cache(3600)

        req = make_request(headers={"cookie": f"hunt_session={sid}"})
        resp = self._run(cached, req)
        assert self._cookies(resp) == []
        assert resp.get_header("Cache-Control") == "public, max-age=3600"

    def test_public_response_that_changes_the_session_is_made_private(self):
        async def cached_but_writes(request):
            request.session().put("k", "v")
            return Response("page").cache(3600)

        resp = self._run(cached_but_writes)
        assert len(self._cookies(resp)) == 1
        assert resp.get_header("Cache-Control") == "private, no-cache"

    def test_session_is_saved_when_the_handler_raises(self):
        async def write_then_raise(request):
            request.session().put("k", "v")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            self._run(write_then_raise)
        (saved,) = list(self.dir.iterdir())
        assert json.loads(saved.read_text())["data"]["k"] == "v"

    def test_revoked_session_gets_no_cookie_and_is_not_rewritten(self):
        sid = self._existing()

        async def slow(request):
            (self.dir / _stored(sid)).unlink()  # revoked while this request is in flight
            request.session().put("more", 1)
            return Response("ok")

        resp = self._run(slow, make_request(headers={"cookie": f"hunt_session={sid}"}))
        assert self._cookies(resp) == []
        assert list(self.dir.iterdir()) == []

    def test_store_io_runs_off_the_event_loop(self):
        import threading

        from hunt.http.middleware import session as session_module
        from hunt.session.store import FileSessionStore

        threads: dict[str, int] = {}

        class Spy(FileSessionStore):
            def start(self, session_id):
                threads["start"] = threading.get_ident()
                super().start(session_id)

            def save(self):
                threads["save"] = threading.get_ident()
                return super().save()

        async def write(request):
            threads["loop"] = threading.get_ident()
            request.session().put("k", "v")
            return Response("ok")

        with patch.object(session_module, "_make_session_store", lambda: Spy(self.dir)):
            self._run(write)
        assert threads["start"] != threads["loop"] and threads["save"] != threads["loop"]

    def test_authenticated_session_touches_the_registry_at_most_once_per_interval(self):
        sid = self._existing()
        touched: list[str] = []

        async def login(request):
            request.session().put("_auth_id", 7)
            return Response("ok")

        req = lambda: make_request(headers={"cookie": f"hunt_session={sid}"})  # noqa: E731
        with patch("hunt.session.registry.SessionRegistry.touch", lambda self, session_id: touched.append(session_id)):
            self._run(login, req())
            self._run(_passthrough, req())
            self._run(_passthrough, req())
        assert touched == [sid]


# ---------------------------------------------------------------------------
# HTTP-06: session registry
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE user_sessions (id VARCHAR(64) PRIMARY KEY, user_id INTEGER, guard VARCHAR(32),"
                " ip_address VARCHAR(45), user_agent TEXT, last_active_at INTEGER)"
            )
        )
        conn.commit()
    monkeypatch.setattr("hunt.session.registry.connection", lambda: engine)
    yield engine
    engine.dispose()


class TestSessionRegistry:
    def test_upsert_sql_is_dialect_aware(self):
        from sqlalchemy.dialects import mysql, postgresql

        from hunt.session.registry import _upsert_sql

        for name in ("mysql", "mariadb"):
            sql = _upsert_sql(name)
            assert "ON DUPLICATE KEY UPDATE last_active_at" in sql and "ON CONFLICT" not in sql
            compiled = str(text(sql).compile(dialect=mysql.dialect()))
            assert "ON DUPLICATE KEY UPDATE" in compiled and compiled.count("%s") == 7
        for name in ("sqlite", "postgresql", ""):
            assert "ON CONFLICT (id) DO UPDATE SET last_active_at" in _upsert_sql(name)
        assert "ON CONFLICT" in str(text(_upsert_sql("postgresql")).compile(dialect=postgresql.dialect()))

    def test_register_uses_the_engine_dialect(self):
        from hunt.session.registry import SessionRegistry

        conn = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        engine = MagicMock()
        engine.dialect.name = "mysql"
        engine.connect.return_value = ctx
        with patch("hunt.session.registry.connection", return_value=engine):
            SessionRegistry().register(SID, 1)
        assert "ON DUPLICATE KEY UPDATE" in str(conn.execute.call_args[0][0])

    def test_round_trip_and_public_id(self, registry_db):
        from hunt.session.registry import SessionRegistry, public_id_for

        reg = SessionRegistry()
        reg.register(SID, 7, "web", "198.51.100.7", "UA")
        reg.register(SID, 7, "web", "198.51.100.7", "UA")  # upsert, not a duplicate-key error
        other = "cd" * 32
        reg.register(other, 8)

        rows, total = reg.all_sessions()
        assert total == 2
        by_id = {row["id"]: row for row in rows}
        expected = hashlib.sha256(SID.encode()).hexdigest()
        # The id column holds the hash, never the raw session id.
        assert SID not in by_id
        assert by_id[expected]["public_id"] == expected == public_id_for(SID)
        assert reg.sessions_for_user(7)[0]["public_id"] == expected
        assert reg.get(SID)["public_id"] == expected
        assert reg.find_by_public_id(expected)["id"] == expected
        assert reg.find_by_public_id("0" * 64) is None
        assert reg.find_by_public_id(SID[:10]) is None

    def test_revoke_by_public_id(self, registry_db):
        from hunt.session.registry import SessionRegistry, public_id_for

        reg = SessionRegistry()
        reg.register(SID, 7)
        reg.register("cd" * 32, 8)
        with patch("hunt.session.registry._delete_session_data") as delete:
            assert reg.revoke_by_public_id(public_id_for(SID)) is True
            assert reg.revoke_by_public_id(public_id_for(SID)) is False
            assert reg.revoke_by_public_id(SID) is False  # the raw id is not a public id
        delete.assert_called_once_with(public_id_for(SID))
        assert reg.get(SID) is None and reg.count() == 1

    def test_revoke_for_user_removes_data_and_rows(self, registry_db):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.register(SID, 7)
        reg.register("cd" * 32, 7)
        reg.register("ef" * 32, 8)
        with patch("hunt.session.registry._delete_session_data") as delete:
            assert reg.revoke_for_user(7) == 2
        assert delete.call_count == 2
        assert reg.sessions_for_user(7) == [] and reg.count() == 1

    def test_revoke_does_not_swallow_failures(self, registry_db):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.register(SID, 7)
        with patch("hunt.session.registry._delete_session_data", side_effect=OSError("disk gone")):
            with pytest.raises(OSError):
                reg.revoke_for_user(7)
        # The row stays so the revocation can be retried.
        assert reg.count() == 1
        with patch("hunt.session.registry.connection", side_effect=RuntimeError("db down")):
            with pytest.raises(RuntimeError):
                reg.revoke_for_user(7)
            with pytest.raises(RuntimeError):
                reg.sessions_for_user(7)

    def test_missing_table_is_tolerated(self, monkeypatch):
        from hunt.session.registry import SessionRegistry

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        monkeypatch.setattr("hunt.session.registry.connection", lambda: engine)
        reg = SessionRegistry()
        reg.register(SID, 7)
        reg.touch(SID)
        reg.deregister(SID)
        assert reg.get(SID) is None
        assert reg.sessions_for_user(7) == []
        assert reg.revoke_for_user(7) == 0
        assert reg.revoke_by_public_id("0" * 64) is False
        assert reg.prune() == 0
        engine.dispose()

    def test_prune_removes_stale_rows(self, registry_db):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.register(SID, 7)
        reg.register("cd" * 32, 8)
        with registry_db.connect() as conn:
            conn.execute(text("UPDATE user_sessions SET last_active_at = 1 WHERE id = :id"), {"id": _stored(SID)})
            conn.commit()
        with patch("hunt.session.registry._delete_session_data") as delete:
            assert reg.prune(max_age=3600) == 1
        delete.assert_called_once_with(_stored(SID))
        assert reg.get(SID) is None and reg.count() == 1

    def test_revoked_file_session_stays_dead(self, tmp_path, registry_db, monkeypatch):
        """End to end: revoke while a request is in flight, then let it finish."""
        from hunt.session.registry import SessionRegistry
        from hunt.session.store import FileSessionStore

        sessions = tmp_path / "sessions"
        monkeypatch.setattr("hunt.session.registry.session_driver", lambda: "file", raising=False)
        monkeypatch.setattr("hunt.session.session_driver", lambda: "file")
        monkeypatch.setattr("hunt.support.helpers.storage_path", lambda *parts: str(tmp_path))
        (tmp_path / "framework").mkdir()

        login = FileSessionStore(sessions)
        login.start(SID)
        login.put("_auth_id", 7)
        login.save()
        sid = login.id
        SessionRegistry().register(sid, 7)

        in_flight = FileSessionStore(sessions)
        in_flight.start(sid)

        with patch("hunt.session.registry._delete_session_file", lambda session_id: (sessions / session_id).unlink()):
            assert SessionRegistry().revoke_for_user(7) == 1
        assert in_flight.save() is False

        after = FileSessionStore(sessions)
        after.start(sid)
        assert after.get("_auth_id") is None and after.id != sid
