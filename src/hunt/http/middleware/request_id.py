from __future__ import annotations

import re
import uuid

from hunt.ctx import request_id as _request_id_var
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

# An inbound ID is echoed into response headers and every log line, so it is
# only accepted when short and made of unremarkable characters.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def current_request_id() -> str:
    return _request_id_var.get()


class RequestId(Middleware):
    """Assign a unique ID to every request.

    Reads X-Request-ID from the incoming request (useful when a load balancer
    or gateway already stamps requests). Generates a UUID4 when it is absent,
    longer than 128 characters, or contains anything but ``A-Z a-z 0-9 . _ : -``.
    Echoes the ID in the X-Request-ID response header.
    """

    reads_body = False  # never touches the request body

    async def handle(self, request: Request, next: Next) -> Response:
        rid = request.header("X-Request-ID", "") or ""
        if not _VALID_REQUEST_ID.match(rid):
            rid = str(uuid.uuid4())
        _request_id_var.set(rid)
        request.request_id = rid  # type: ignore[attr-defined]
        response = await next(request)
        response.header("X-Request-ID", rid)
        return response
