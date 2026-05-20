from __future__ import annotations

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

_CHALLENGE_PATHS = {"/two-factor/challenge"}
_EXEMPT_PREFIXES = ("/login", "/logout", "/two-factor/challenge")


class EnsureTwoFactorAuthenticated(Middleware):
    """Redirect users with a pending 2FA challenge to the challenge page."""

    async def handle(self, request: Request, next: Next) -> Response:
        path = request.path.rstrip("/") or "/"
        for prefix in _EXEMPT_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return await next(request)

        session = getattr(request, "_session", None)
        if session and session.get("_2fa_pending"):
            from hunt.http.response import RedirectResponse

            return RedirectResponse("/two-factor/challenge", 302)

        return await next(request)
