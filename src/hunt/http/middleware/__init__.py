from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.http.request import Request
    from hunt.http.response import Response

Next = Callable[["Request"], Awaitable["Response"]]


class Middleware:
    #: Whether ``handle`` may use the request body (``input``, ``all``, ``json``,
    #: ``file``, ``body``...). The kernel reads the body before running such a
    #: middleware. A middleware that never looks at the body sets this to False,
    #: so a request it rejects (rate limit, authentication...) is answered without
    #: the body being read; it can still ``await request.load_body()`` itself.
    reads_body: bool = True

    async def handle(self, request: Request, next: Next) -> Response:
        return await next(request)
