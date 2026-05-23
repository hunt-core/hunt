"""Phase E — HTTP Client tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw(status: int = 200, body: str = "", headers: dict | None = None) -> Any:
    """Build a fake httpx.Response to wrap in HttpResponse."""
    import httpx

    content = body.encode() if isinstance(body, str) else body
    h = {"content-type": "text/plain"}
    h.update(headers or {})
    return httpx.Response(status, content=content, headers=h)


def _make_raw_json(data: Any, status: int = 200) -> Any:
    import json

    import httpx

    content = json.dumps(data).encode()
    return httpx.Response(status, content=content, headers={"content-type": "application/json"})


# ===========================================================================
# 1. HttpResponse wrapper
# ===========================================================================

class TestHttpResponse:
    def test_status_code(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw(201))
        assert r.status_code == 201

    def test_body_returns_text(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw(200, "hello"))
        assert r.body() == "hello"
        assert r.text() == "hello"

    def test_json_parses(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw_json({"key": "value"}))
        assert r.json() == {"key": "value"}

    def test_json_key_access(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw_json({"name": "Alice", "age": 30}))
        assert r.json("name") == "Alice"
        assert r.json("missing") is None

    def test_content_returns_bytes(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw(200, "bytes"))
        assert isinstance(r.content(), bytes)

    # ---- status helpers --------------------------------------------------

    def test_ok_true_for_2xx(self):
        from hunt.http.client import HttpResponse
        assert HttpResponse(_make_raw(200)).ok()
        assert HttpResponse(_make_raw(201)).ok()
        assert HttpResponse(_make_raw(204)).ok()

    def test_ok_false_for_4xx(self):
        from hunt.http.client import HttpResponse
        assert not HttpResponse(_make_raw(404)).ok()

    def test_successful_alias_of_ok(self):
        from hunt.http.client import HttpResponse
        assert HttpResponse(_make_raw(200)).successful()
        assert not HttpResponse(_make_raw(500)).successful()

    def test_redirect(self):
        from hunt.http.client import HttpResponse
        assert HttpResponse(_make_raw(301)).redirect()
        assert not HttpResponse(_make_raw(200)).redirect()

    def test_failed_true_for_4xx_and_5xx(self):
        from hunt.http.client import HttpResponse
        assert HttpResponse(_make_raw(400)).failed()
        assert HttpResponse(_make_raw(500)).failed()
        assert not HttpResponse(_make_raw(200)).failed()

    def test_client_error(self):
        from hunt.http.client import HttpResponse
        assert HttpResponse(_make_raw(404)).client_error()
        assert not HttpResponse(_make_raw(500)).client_error()

    def test_server_error(self):
        from hunt.http.client import HttpResponse
        assert HttpResponse(_make_raw(503)).server_error()
        assert not HttpResponse(_make_raw(404)).server_error()

    def test_throw_raises_on_failure(self):
        from hunt.http.client import HttpResponse, RequestException
        r = HttpResponse(_make_raw(422))
        with pytest.raises(RequestException) as exc_info:
            r.throw()
        assert exc_info.value.response is r

    def test_throw_returns_self_on_success(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw(200))
        assert r.throw() is r

    def test_bool_true_for_ok(self):
        from hunt.http.client import HttpResponse
        assert bool(HttpResponse(_make_raw(200)))
        assert not bool(HttpResponse(_make_raw(404)))

    def test_repr(self):
        from hunt.http.client import HttpResponse
        assert "200" in repr(HttpResponse(_make_raw(200)))

    # ---- headers ---------------------------------------------------------

    def test_headers_dict(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw(200, "", {"x-custom": "yes"}))
        assert r.headers.get("x-custom") == "yes"

    def test_header_method(self):
        from hunt.http.client import HttpResponse
        r = HttpResponse(_make_raw(200, "", {"x-id": "42"}))
        assert r.header("x-id") == "42"
        assert r.header("missing", "default") == "default"


# ===========================================================================
# 2. Http.response() static builder
# ===========================================================================

class TestHttpResponseStatic:
    def test_dict_body_sets_json_content_type(self):
        from hunt.http.client import Http
        r = Http.response({"id": 1}, 201)
        assert r.status_code == 201
        assert r.json("id") == 1
        assert "application/json" in r.header("content-type", "")

    def test_list_body(self):
        from hunt.http.client import Http
        r = Http.response([1, 2, 3], 200)
        assert r.json() == [1, 2, 3]

    def test_string_body(self):
        from hunt.http.client import Http
        r = Http.response("Hello World", 200)
        assert r.body() == "Hello World"

    def test_bytes_body(self):
        from hunt.http.client import Http
        r = Http.response(b"raw bytes", 200)
        assert r.content() == b"raw bytes"

    def test_empty_body_defaults(self):
        from hunt.http.client import Http
        r = Http.response()
        assert r.status_code == 200
        assert r.body() == ""

    def test_custom_headers_forwarded(self):
        from hunt.http.client import Http
        r = Http.response("ok", 200, {"X-Request-Id": "abc"})
        assert r.header("x-request-id") == "abc"


# ===========================================================================
# 3. PendingRequest builder
# ===========================================================================

class TestPendingRequestBuilder:
    def _pending(self):
        from hunt.http.client import Http, PendingRequest
        return PendingRequest(Http)

    def test_with_headers_stored(self):
        p = self._pending().with_headers({"X-Foo": "bar"})
        assert p._headers["X-Foo"] == "bar"

    def test_with_token_bearer(self):
        p = self._pending().with_token("my-token")
        assert p._headers["Authorization"] == "Bearer my-token"

    def test_with_token_custom_type(self):
        p = self._pending().with_token("key123", "Token")
        assert p._headers["Authorization"] == "Token key123"

    def test_with_basic_auth(self):
        import base64
        p = self._pending().with_basic_auth("alice", "secret")
        expected = "Basic " + base64.b64encode(b"alice:secret").decode()
        assert p._headers["Authorization"] == expected

    def test_accept_sets_header(self):
        p = self._pending().accept("text/xml")
        assert p._headers["Accept"] == "text/xml"

    def test_accept_json(self):
        p = self._pending().accept_json()
        assert p._headers["Accept"] == "application/json"

    def test_as_json_sets_format(self):
        p = self._pending().as_json()
        assert p._body_format == "json"

    def test_as_form_sets_format(self):
        p = self._pending().as_form()
        assert p._body_format == "form"

    def test_timeout_stored(self):
        p = self._pending().timeout(5.0)
        assert p._timeout == 5.0

    def test_retry_stored(self):
        p = self._pending().retry(3, 500)
        assert p._retry_times == 3
        assert p._retry_sleep_ms == 500

    def test_builder_methods_return_self(self):
        p = self._pending()
        assert p.with_headers({}) is p
        assert p.with_token("t") is p
        assert p.accept("x") is p
        assert p.timeout(1) is p
        assert p.retry(1) is p


# ===========================================================================
# 4. Http facade proxy
# ===========================================================================

class TestHttpFacadeProxy:
    def test_with_headers_returns_pending_request(self):
        from hunt.http.client import Http, PendingRequest
        p = Http.with_headers({"X-A": "1"})
        assert isinstance(p, PendingRequest)
        assert p._headers["X-A"] == "1"

    def test_with_token_returns_pending_request(self):
        from hunt.http.client import Http, PendingRequest
        p = Http.with_token("tok")
        assert isinstance(p, PendingRequest)

    def test_timeout_returns_pending_request(self):
        from hunt.http.client import Http, PendingRequest
        p = Http.timeout(10)
        assert isinstance(p, PendingRequest)
        assert p._timeout == 10

    def test_accept_json_returns_pending_request(self):
        from hunt.http.client import Http, PendingRequest
        p = Http.accept_json()
        assert isinstance(p, PendingRequest)

    def test_each_call_creates_fresh_pending_request(self):
        from hunt.http.client import Http
        p1 = Http.with_headers({"X-A": "1"})
        p2 = Http.with_headers({"X-B": "2"})
        assert "X-A" not in p2._headers
        assert "X-B" not in p1._headers


# ===========================================================================
# 5. Http.fake() — fake mode
# ===========================================================================

class TestHttpFake:
    def setup_method(self):
        from hunt.http.client import Http
        Http.unfake()

    def teardown_method(self):
        from hunt.http.client import Http
        Http.unfake()

    def test_fake_intercepts_requests(self):
        from hunt.http.client import Http
        Http.fake({"https://api.example.com/users": Http.response({"users": []}, 200)})
        resp = Http.get("https://api.example.com/users")
        assert resp.status_code == 200
        assert resp.json("users") == []

    def test_fake_wildcard_matching(self):
        from hunt.http.client import Http
        Http.fake({"https://api.example.com/*": Http.response({"ok": True}, 200)})
        resp = Http.get("https://api.example.com/posts/42")
        assert resp.ok()
        assert resp.json("ok") is True

    def test_fake_exact_beats_wildcard(self):
        from hunt.http.client import Http
        Http.fake({
            "https://api.example.com/*": Http.response("wildcard", 200),
            "https://api.example.com/special": Http.response("exact", 201),
        })
        resp = Http.get("https://api.example.com/special")
        assert resp.status_code == 201
        assert resp.body() == "exact"

    def test_fake_fallback_returns_200_empty(self):
        from hunt.http.client import Http
        Http.fake()  # no patterns — stub everything
        resp = Http.get("https://any.url/path")
        assert resp.status_code == 200

    def test_fake_records_requests(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://example.com/a")
        Http.post("https://example.com/b", {"x": 1})
        assert len(Http.recorded()) == 2

    def test_fake_records_method(self):
        from hunt.http.client import Http
        Http.fake()
        Http.post("https://example.com/data", {"a": 1})
        records = Http.recorded()
        assert records[0]["method"] == "POST"
        assert records[0]["url"] == "https://example.com/data"

    def test_assert_sent_passes_when_matched(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://api.example.com/users")
        Http.assert_sent("https://api.example.com/*")

    def test_assert_sent_with_count(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://api.example.com/users")
        Http.get("https://api.example.com/users")
        Http.assert_sent("https://api.example.com/*", times=2)

    def test_assert_sent_fails_when_no_match(self):
        from hunt.http.client import Http
        Http.fake()
        with pytest.raises(AssertionError):
            Http.assert_sent("https://missing.example.com/*")

    def test_assert_not_sent_passes_when_absent(self):
        from hunt.http.client import Http
        Http.fake()
        Http.assert_not_sent("https://never.example.com/*")

    def test_assert_not_sent_fails_when_present(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://example.com/data")
        with pytest.raises(AssertionError):
            Http.assert_not_sent("https://example.com/*")

    def test_assert_nothing_sent_passes_when_empty(self):
        from hunt.http.client import Http
        Http.fake()
        Http.assert_nothing_sent()

    def test_assert_nothing_sent_fails_when_requests_made(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://example.com")
        with pytest.raises(AssertionError):
            Http.assert_nothing_sent()

    def test_unfake_clears_recorded(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://example.com")
        Http.unfake()
        assert Http._fakes is None
        assert Http._recorded == []

    def test_recorded_filtered_by_pattern(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://api.example.com/users")
        Http.get("https://other.example.com/data")
        matched = Http.recorded("https://api.example.com/*")
        assert len(matched) == 1
        assert matched[0]["url"] == "https://api.example.com/users"

    def test_fake_all_verbs_recorded(self):
        from hunt.http.client import Http
        Http.fake()
        Http.get("https://example.com/a")
        Http.post("https://example.com/b")
        Http.put("https://example.com/c")
        Http.patch("https://example.com/d")
        Http.delete("https://example.com/e")
        assert len(Http.recorded()) == 5

    def test_fake_glob_star_matches_all(self):
        from hunt.http.client import Http
        Http.fake({"*": Http.response("catch-all", 418)})
        resp = Http.get("https://anywhere.com/path")
        assert resp.status_code == 418
        assert resp.body() == "catch-all"


# ===========================================================================
# 6. Real requests (mocked at _do_send level)
# ===========================================================================

class TestRealRequests:
    def _mock_send(self, status: int = 200, body: str = "") -> MagicMock:
        raw = _make_raw(status, body)
        from hunt.http.client import HttpResponse
        mock = MagicMock(return_value=HttpResponse(raw))
        return mock

    def test_get_passes_params(self):
        from hunt.http.client import Http

        captured = {}

        def fake_do_send(method, url, params=None, data=None):
            captured["params"] = params
            from hunt.http.client import HttpResponse
            return HttpResponse(_make_raw(200))

        p = Http._pending()
        with patch.object(p, "_do_send", fake_do_send):
            p.get("https://example.com", params={"q": "test"})

        assert captured["params"] == {"q": "test"}

    def test_post_sends_json_data(self):
        from hunt.http.client import Http

        captured = {}

        def fake_do_send(method, url, params=None, data=None):
            captured["method"] = method
            captured["data"] = data
            from hunt.http.client import HttpResponse
            return HttpResponse(_make_raw(201))

        p = Http._pending()
        with patch.object(p, "_do_send", fake_do_send):
            p.post("https://example.com", {"name": "Alice"})

        assert captured["method"] == "POST"
        assert captured["data"] == {"name": "Alice"}

    def test_retry_on_exception(self):
        from hunt.http.client import Http, HttpResponse

        call_count = [0]

        def fake_do_send(method, url, params=None, data=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("timeout")
            return HttpResponse(_make_raw(200))

        p = Http._pending().retry(2, sleep_ms=0)
        with patch.object(p, "_do_send", fake_do_send):
            resp = p.get("https://example.com")

        assert call_count[0] == 3
        assert resp.ok()

    def test_retry_exhausted_raises(self):
        from hunt.http.client import Http

        def fake_do_send(method, url, params=None, data=None):
            raise ConnectionError("always fails")

        p = Http._pending().retry(2, sleep_ms=0)
        with patch.object(p, "_do_send", fake_do_send):
            with pytest.raises(ConnectionError):
                p.get("https://example.com")

    def test_retry_when_condition(self):
        from hunt.http.client import Http, HttpResponse

        call_count = [0]

        def fake_do_send(method, url, params=None, data=None):
            call_count[0] += 1
            status = 503 if call_count[0] < 3 else 200
            return HttpResponse(_make_raw(status))

        p = Http._pending().retry(3, sleep_ms=0, when=lambda r: r.server_error())
        with patch.object(p, "_do_send", fake_do_send):
            resp = p.get("https://example.com")

        assert call_count[0] == 3
        assert resp.ok()

    def test_no_retry_by_default(self):
        from hunt.http.client import Http

        call_count = [0]

        def fake_do_send(method, url, params=None, data=None):
            call_count[0] += 1
            raise ConnectionError("fail")

        p = Http._pending()  # retry_times = 0
        with patch.object(p, "_do_send", fake_do_send):
            with pytest.raises(ConnectionError):
                p.get("https://example.com")

        assert call_count[0] == 1  # no retry


# ===========================================================================
# 7. RequestException
# ===========================================================================

class TestRequestException:
    def test_carries_response(self):
        from hunt.http.client import HttpResponse, RequestException
        resp = HttpResponse(_make_raw(500))
        exc = RequestException(resp)
        assert exc.response is resp

    def test_message_includes_status(self):
        from hunt.http.client import HttpResponse, RequestException
        resp = HttpResponse(_make_raw(422))
        exc = RequestException(resp)
        assert "422" in str(exc)
