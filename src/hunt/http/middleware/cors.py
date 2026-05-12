from __future__ import annotations

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response


class HandleCors(Middleware):
    """Cross-Origin Resource Sharing headers.

    Defaults to denying all cross-origin requests. Set `allowed_origins` to a
    list of permitted origins (e.g. ["https://app.example.com"]).

    Do NOT combine `allowed_origins = ["*"]` with `allow_credentials = True` —
    browsers reject such responses and it indicates a configuration error.
    """

    allowed_origins: list[str] = []  # deny-by-default
    allowed_methods: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    allowed_headers: list[str] = ["Content-Type", "Authorization", "X-CSRF-TOKEN"]
    expose_headers: list[str] = []
    allow_credentials: bool = False
    max_age: int = 0

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if "*" in cls.allowed_origins and cls.allow_credentials:
            raise ValueError(
                "HandleCors: allow_credentials=True cannot be used with allowed_origins=['*']. "
                "Specify explicit origin URLs instead."
            )

    async def handle(self, request: Request, next: Next) -> Response:
        origin = request.header("Origin", "")

        if request.method == "OPTIONS":
            response = Response("", 204)
        else:
            response = await next(request)

        self._add_cors_headers(response, origin)
        return response

    def _add_cors_headers(self, response: Response, origin: str) -> None:
        if "*" in self.allowed_origins:
            response.header("Access-Control-Allow-Origin", "*")
        elif origin and origin in self.allowed_origins:
            response.header("Access-Control-Allow-Origin", origin)
            response.header("Vary", "Origin")
        else:
            return  # no CORS headers for disallowed origins

        response.header("Access-Control-Allow-Methods", ", ".join(self.allowed_methods))
        response.header("Access-Control-Allow-Headers", ", ".join(self.allowed_headers))

        if self.expose_headers:
            response.header("Access-Control-Expose-Headers", ", ".join(self.expose_headers))
        if self.allow_credentials:
            response.header("Access-Control-Allow-Credentials", "true")
        if self.max_age:
            response.header("Access-Control-Max-Age", str(self.max_age))
