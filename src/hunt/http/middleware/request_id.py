from __future__ import annotations

import uuid

from hunt.ctx import request_id as _request_id_var
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response


def current_request_id() -> str:
    return _request_id_var.get()


class RequestId(Middleware):
    """Assign a unique ID to every request.

    Reads X-Request-ID from the incoming request (useful when a load balancer
    or gateway already stamps requests). Generates a UUID4 when absent.
    Echoes the ID in the X-Request-ID response header.
    """

    async def handle(self, request: Request, next: Next) -> Response:
        rid = request.header("X-Request-ID", "") or str(uuid.uuid4())
        _request_id_var.set(rid)
        request.request_id = rid  # type: ignore[attr-defined]
        response = await next(request)
        response.header("X-Request-ID", rid)
        return response
