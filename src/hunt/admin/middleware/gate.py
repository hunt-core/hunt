from __future__ import annotations

import logging

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import JsonResponse, RedirectResponse, Response

_log = logging.getLogger(__name__)


class AdminGate(Middleware):
    """Guard all admin routes.

    If Admin._gate is set, that callable is used for the decision.
    Otherwise, it falls back to checking Auth.check() (any logged-in user).
    Denied requests get a 403 JSON response or a redirect to /login depending
    on whether the client prefers JSON.
    """

    async def handle(self, request: Request, next: Next) -> Response:
        from hunt.admin.application import Admin

        allowed = False
        if Admin._gate is not None:
            try:
                allowed = bool(Admin._gate(request))
            except Exception:
                _log.exception("AdminGate: gate callable raised an exception — denying access")
                allowed = False
        else:
            _log.warning(
                "AdminGate: no gate configured — denying all access. Call Admin.gate(fn) in your routes/admin.py."
            )
            allowed = False

        if not allowed:
            if request.expects_json():
                return JsonResponse({"message": "Forbidden."}, status=403)
            return RedirectResponse("/login")

        return await next(request)
