from __future__ import annotations

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, RedirectResponse, Response


class Authenticate(Middleware):
    """Redirect unauthenticated requests to the login page."""

    redirect_to: str = "/login"

    async def handle(self, request: Request, next: Next) -> Response:
        from hunt.auth.manager import Auth

        if not Auth.check():
            if request.expects_json():
                raise HttpException(401, "Unauthenticated.")
            return RedirectResponse(self.redirect_to)

        return await next(request)
