from __future__ import annotations

import inspect
import logging

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import JsonResponse, RedirectResponse, Response

_log = logging.getLogger(__name__)


class AdminGate(Middleware):
    """Guard all admin routes.

    The callable registered with ``Admin.gate(fn)`` decides: it receives the
    request and must return a truthy value to allow access. It may be a plain
    function or an ``async def``; an awaitable result is awaited before the
    decision is taken. When no gate is configured, or the gate raises, access
    is denied (there is no "any logged-in user" fallback).

    Denied requests get a 403 JSON response when the client expects JSON,
    otherwise a redirect to ``/login``.
    """

    async def handle(self, request: Request, next: Next) -> Response:
        from hunt.admin.application import Admin

        allowed = False
        if Admin._gate is not None:
            try:
                result = Admin._gate(request)
                if inspect.isawaitable(result):
                    result = await result
                allowed = bool(result)
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
