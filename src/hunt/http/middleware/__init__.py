from __future__ import annotations

from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.http.request import Request
    from hunt.http.response import Response

Next = Callable[["Request"], Awaitable["Response"]]


class Middleware:
    async def handle(self, request: "Request", next: Next) -> "Response":
        return await next(request)
