from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.http.request import Request
    from hunt.http.response import Response

Next = Callable[["Request"], Awaitable["Response"]]


class Middleware:
    async def handle(self, request: Request, next: Next) -> Response:
        return await next(request)
