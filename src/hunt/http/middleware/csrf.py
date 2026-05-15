from __future__ import annotations

import hmac
from typing import ClassVar

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class VerifyCsrfToken(Middleware):
    """Validate CSRF token on state-changing requests."""

    except_urls: ClassVar[list[str]] = []

    async def handle(self, request: Request, next: Next) -> Response:
        if request.method in _SAFE_METHODS or self._is_exempt(request):
            return await next(request)

        if not self._tokens_match(request):
            raise HttpException(419, "CSRF token mismatch.")

        return await next(request)

    def _is_exempt(self, request: Request) -> bool:
        path = request.path
        for prefix in self.except_urls:
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return True
        return False

    def _tokens_match(self, request: Request) -> bool:
        session = getattr(request, "_session", None)
        if session is None:
            return False
        session_token = session.get("_csrf_token")
        if not session_token:
            return False
        request_token = request.input("_token") or request.header("X-CSRF-TOKEN")
        try:
            return bool(request_token and hmac.compare_digest(str(session_token), str(request_token)))
        except TypeError:
            return False
