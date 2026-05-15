from __future__ import annotations

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import RedirectResponse, Response


class EnsureEmailIsVerified(Middleware):
    """Redirect unverified users to the email verification notice page."""

    redirect_to: str = "/email/verify"

    async def handle(self, request: Request, next: Next) -> Response:
        from hunt.auth.manager import Auth
        from hunt.auth.verification import EmailVerification

        user = Auth.user()
        if user is None or not EmailVerification.is_verified(user):
            return RedirectResponse(self.redirect_to)
        return await next(request)
