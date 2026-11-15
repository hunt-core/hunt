from __future__ import annotations

from typing import ClassVar

from hunt.http.middleware import Next
from hunt.http.middleware.csrf import VerifyCsrfToken
from hunt.http.request import Request
from hunt.http.response import Response

# Request attribute set once a CSRF check has passed for this request. A
# global VerifyCsrfToken that sets it lets the admin layer skip its own check.
_VERIFIED_ATTR = "_csrf_verified"


class AdminVerifyCsrfToken(VerifyCsrfToken):
    """CSRF protection attached to the admin route group itself.

    The admin panel must not depend on the host application listing
    ``VerifyCsrfToken`` in its global middleware. This runs on every admin
    route; it never honours ``except_urls`` (no admin route is exempt).

    When the application also runs ``VerifyCsrfToken`` globally the token is
    simply compared a second time: the check only reads the session, so
    running it twice has no side effect. A CSRF middleware that marks the
    request with ``request._csrf_verified = True`` is not re-run.
    """

    except_urls: ClassVar[list[str]] = []

    async def handle(self, request: Request, next: Next) -> Response:
        if getattr(request, _VERIFIED_ATTR, False) is True:
            return await next(request)
        response = await super().handle(request, next)
        return response

    def _is_exempt(self, request: Request) -> bool:
        return False
