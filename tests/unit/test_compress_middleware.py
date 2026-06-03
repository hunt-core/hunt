from __future__ import annotations

import gzip

import pytest

from hunt.http.middleware.compress import CompressResponse
from hunt.http.response import JsonResponse, Response


class _FakeRequest:
    """Minimal stand-in exposing only what the middleware reads."""

    def __init__(self, accept_encoding: str = "gzip, deflate") -> None:
        self._accept = accept_encoding

    def header(self, name: str, default=None):
        if name.lower() == "accept-encoding":
            return self._accept
        return default


def _run(middleware: CompressResponse, request, response: Response) -> Response:
    import asyncio

    async def _next(_req):
        return response

    return asyncio.run(middleware.handle(request, _next))


class TestCompressResponse:
    def test_compresses_large_text_response(self):
        body = "<html>" + ("x" * 5000) + "</html>"
        out = _run(CompressResponse(), _FakeRequest(), Response(body))

        assert out._headers.get("Content-Encoding") == "gzip"
        assert "Accept-Encoding" in out._headers.get("Vary", "")
        assert gzip.decompress(out._body).decode() == body
        assert len(out._body) < len(body.encode())

    def test_compresses_json(self):
        resp = JsonResponse({"items": list(range(2000))})
        original = resp._body
        out = _run(CompressResponse(), _FakeRequest(), resp)

        assert out._headers.get("Content-Encoding") == "gzip"
        assert gzip.decompress(out._body) == original

    def test_skips_when_client_does_not_accept_gzip(self):
        body = "y" * 5000
        out = _run(CompressResponse(), _FakeRequest(accept_encoding="identity"), Response(body))

        assert "Content-Encoding" not in out._headers
        assert out._body == body.encode()

    def test_skips_small_bodies(self):
        out = _run(CompressResponse(), _FakeRequest(), Response("tiny"))
        assert "Content-Encoding" not in out._headers

    def test_skips_non_text_content_type(self):
        big = b"\x89PNG" + b"\x00" * 5000
        resp = Response(big, content_type="image/png")
        out = _run(CompressResponse(), _FakeRequest(), resp)
        assert "Content-Encoding" not in out._headers
        assert out._body == big

    def test_does_not_double_encode(self):
        body = "z" * 5000
        resp = Response(body)
        resp.header("Content-Encoding", "br")
        out = _run(CompressResponse(), _FakeRequest(), resp)
        # Left untouched — already encoded.
        assert out._headers["Content-Encoding"] == "br"
        assert out._body == body.encode()

    def test_skips_304(self):
        resp = Response("a" * 5000, status=304)
        out = _run(CompressResponse(), _FakeRequest(), resp)
        assert "Content-Encoding" not in out._headers

    def test_respects_disable_env(self, monkeypatch):
        monkeypatch.setenv("GZIP_ENABLED", "false")
        out = _run(CompressResponse(), _FakeRequest(), Response("q" * 5000))
        assert "Content-Encoding" not in out._headers

    def test_appends_to_existing_vary(self):
        resp = Response("v" * 5000)
        resp.header("Vary", "Cookie")
        out = _run(CompressResponse(), _FakeRequest(), resp)
        vary = out._headers["Vary"]
        assert "Cookie" in vary and "Accept-Encoding" in vary

    def test_honors_min_length_env(self, monkeypatch):
        monkeypatch.setenv("GZIP_MIN_LENGTH", "100000")
        out = _run(CompressResponse(), _FakeRequest(), Response("w" * 5000))
        assert "Content-Encoding" not in out._headers


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
