from __future__ import annotations

import hmac
from typing import ClassVar

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, Response

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class VerifyCsrfToken(Middleware):
    """Validate CSRF token on state-changing requests.

    The request body is only read when the form's ``_token`` field is needed:
    a request carrying a valid ``X-CSRF-TOKEN`` header is verified without it.
    """

    reads_body = False  # loads the body itself, and only when the form token is needed

    except_urls: ClassVar[list[str]] = []

    async def handle(self, request: Request, next: Next) -> Response:
        if request.method in _SAFE_METHODS or self._is_exempt(request):
            return await next(request)

        if self._needs_form_token(request):
            loader = getattr(request, "load_body", None)
            if loader is not None:
                await loader()

        if not self._tokens_match(request):
            raise HttpException(419, "CSRF token mismatch.")

        # Lets a nested CSRF layer (the admin group's own check) skip repeating it.
        request._csrf_verified = True
        return await next(request)

    def _is_exempt(self, request: Request) -> bool:
        path = request.path
        for prefix in self.except_urls:
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return True
        return False

    @staticmethod
    def _session_token(request: Request) -> str | None:
        session = getattr(request, "_session", None)
        if session is None:
            return None
        return session.get("_csrf_token") or None

    @staticmethod
    def _same(session_token: object, request_token: object) -> bool:
        try:
            return bool(request_token and hmac.compare_digest(str(session_token), str(request_token)))
        except TypeError:
            return False

    def _needs_form_token(self, request: Request) -> bool:
        """True when only the form body can still make the check pass: there is
        a session token and the ``X-CSRF-TOKEN`` header is absent or wrong."""
        session_token = self._session_token(request)
        if not session_token:
            return False
        return not self._same(session_token, request.header("X-CSRF-TOKEN"))

    def _tokens_match(self, request: Request) -> bool:
        session_token = self._session_token(request)
        if not session_token:
            return False
        # Body or header only: a token in the query string would end up in
        # logs, history and Referer headers.
        if self._same(session_token, request.header("X-CSRF-TOKEN")):
            return True
        body_input = getattr(request, "_body_input", None)
        body_token = body_input().get("_token") if callable(body_input) else request.input("_token")
        return self._same(session_token, body_token)
