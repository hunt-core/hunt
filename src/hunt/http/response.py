from __future__ import annotations

import json
from typing import Any


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

    def header(self, key: str, value: str) -> "Response":
        self._headers[key] = value
        return self

    def with_cookie(
        self,
        name: str,
        value: str,
        max_age: int = 0,
        path: str = "/",
        http_only: bool = True,
        same_site: str = "Lax",
    ) -> "Response":
        cookie = f"{name}={value}; Path={path}; SameSite={same_site}"
        if max_age:
            cookie += f"; Max-Age={max_age}"
        if http_only:
            cookie += "; HttpOnly"
        self._cookie_headers.append(cookie)
        return self

    def forget_cookie(self, name: str, path: str = "/") -> "Response":
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
        await send({
            "type": "http.response.start",
            "status": self.status,
            "headers": self._asgi_headers(),
        })
        await send({
            "type": "http.response.body",
            "body": self._body,
        })


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


def response(content: str = "", status: int = 200, headers: dict | None = None) -> Response:
    return Response(content, status, headers)


def json_response(data: Any, status: int = 200) -> JsonResponse:
    return JsonResponse(data, status)


def redirect(url: str, status: int = 302) -> RedirectResponse:
    return RedirectResponse(url, status)
