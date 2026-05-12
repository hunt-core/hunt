from __future__ import annotations

import html
import os
import traceback
from pathlib import Path
from typing import Any

from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse, Response


class ExceptionHandler:
    """Converts exceptions to HTTP responses. Override render() to customise."""

    def __init__(self, debug: bool = False, views_path: Path | None = None) -> None:
        self.debug = debug
        self.views_path = views_path

    def render(self, request: Request, exc: Exception) -> Response:
        if isinstance(exc, HttpException):
            return self._http_error(request, exc)
        return self._server_error(request, exc)

    # ------------------------------------------------------------------

    def _http_error(self, request: Request, exc: HttpException) -> Response:
        if request.expects_json():
            return JsonResponse({"error": exc.message or self._status_text(exc.status)}, exc.status)
        custom = self._custom_view(exc.status)
        if custom:
            return Response(custom, exc.status)
        return Response(self._default_html(exc.status, exc.message), exc.status)

    def _server_error(self, request: Request, exc: Exception) -> Response:
        if request.expects_json():
            body: dict[str, Any] = {"error": "Server Error"}
            if self.debug:
                body["exception"] = str(exc)
                body["trace"] = traceback.format_exc()
            return JsonResponse(body, 500)

        custom = self._custom_view(500)
        if custom and not self.debug:
            return Response(custom, 500)

        if self.debug:
            tb = html.escape(traceback.format_exc())
            exc_type = html.escape(type(exc).__name__)
            exc_msg = html.escape(str(exc))
            debug_html = (
                f"<html><body style='font-family:monospace;padding:2rem'>"
                f"<h2 style='color:#c0392b'>{exc_type}: {exc_msg}</h2>"
                f"<pre style='background:#f8f8f8;padding:1rem;overflow:auto'>{tb}</pre>"
                f"</body></html>"
            )
            return Response(debug_html, 500)

        return Response(self._default_html(500, "Internal Server Error"), 500)

    def _custom_view(self, status: int) -> str | None:
        if self.views_path is None:
            return None
        p = self.views_path / "errors" / f"{status}.html"
        if p.exists():
            return p.read_text()
        return None

    @staticmethod
    def _default_html(status: int, message: str) -> str:
        safe_text = html.escape(message or ExceptionHandler._status_text(status))
        return (
            f"<!DOCTYPE html><html><head><title>{status}</title>"
            f"<style>body{{font-family:system-ui;display:flex;align-items:center;justify-content:center;"
            f"min-height:100vh;margin:0;background:#f9fafb}}"
            f"div{{text-align:center}}h1{{font-size:4rem;margin:0;color:#374151}}"
            f"p{{color:#6b7280;margin:.5rem 0 0}}</style></head>"
            f"<body><div><h1>{status}</h1><p>{safe_text}</p></div></body></html>"
        )

    @staticmethod
    def _status_text(status: int) -> str:
        return {
            400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
            404: "Not Found", 405: "Method Not Allowed", 419: "Page Expired",
            422: "Unprocessable Entity", 429: "Too Many Requests",
            500: "Internal Server Error", 503: "Service Unavailable",
        }.get(status, "Error")
