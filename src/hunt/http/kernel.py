from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Type

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse, Response
from hunt.http.router import Router, RouteNotFoundException


class HttpKernel:
    """ASGI application entry point."""

    def __init__(
        self,
        router: Router,
        global_middleware: list[Any] | None = None,
        exception_handler: Any | None = None,
    ) -> None:
        self._router = router
        self._global_middleware: list[Any] = global_middleware or []
        self._exception_handler = exception_handler

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope["type"] != "http":
            return

        body = await self._read_body(receive)
        request = Request(scope, body)

        resp = await self._handle(request)
        await resp(scope, receive, send)

    async def _handle(self, request: Request) -> Response:
        from hunt.auth.manager import _set_request
        _set_request(request)

        try:
            route, params = self._router.dispatch(request.method, request.path)
        except RouteNotFoundException:
            return self._render_error(request, HttpException(404, "Not Found"))

        request.set_route_params(params)

        all_middleware = [*self._global_middleware, *route.middleware]
        handler = self._make_handler(route.action, params)
        pipeline = self._build_pipeline(all_middleware, handler)

        try:
            return await pipeline(request)
        except HttpException as e:
            return self._render_error(request, e)
        except Exception as e:
            return self._render_exception(request, e)

    def _make_handler(self, action: Callable, params: dict[str, str]) -> Next:
        async def handler(request: Request) -> Response:
            sig = inspect.signature(action)
            kwargs: dict[str, Any] = {}
            for name, param in sig.parameters.items():
                if name in ("request", "req"):
                    kwargs[name] = request
                elif name in params:
                    kwargs[name] = params[name]
                elif param.default is not inspect.Parameter.empty:
                    kwargs[name] = param.default

            result = action(**kwargs)
            if inspect.iscoroutine(result):
                result = await result

            if isinstance(result, Response):
                return result
            if isinstance(result, dict):
                return JsonResponse(result)
            if isinstance(result, str):
                from hunt.http.response import response as make_response
                return make_response(result)
            return Response(str(result) if result is not None else "")

        return handler

    def _build_pipeline(self, middleware_list: list[Any], handler: Next) -> Next:
        pipeline = handler
        for mw in reversed(middleware_list):
            instance = mw() if isinstance(mw, type) else mw
            current_next = pipeline

            def make_layer(m: Middleware, nxt: Next) -> Next:
                async def layer(request: Request) -> Response:
                    return await m.handle(request, nxt)
                return layer

            pipeline = make_layer(instance, current_next)
        return pipeline

    _MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB

    @classmethod
    async def _read_body(cls, receive: Any) -> bytes:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > cls._MAX_BODY_BYTES:
                raise HttpException(413, "Request body too large.")
            if not message.get("more_body", False):
                break
        return body

    @staticmethod
    async def _handle_lifespan(scope: dict, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _render_error(self, request: Request, exc: HttpException) -> Response:
        if self._exception_handler:
            return self._exception_handler.render(request, exc)
        if request.expects_json():
            return JsonResponse({"error": exc.message or "Error"}, exc.status)
        return Response(f"<h1>{exc.status}</h1><p>{exc.message}</p>", exc.status)

    def _render_exception(self, request: Request, exc: Exception) -> Response:
        if self._exception_handler:
            return self._exception_handler.render(request, exc)
        import html, traceback, os
        if os.environ.get("APP_DEBUG", "false").lower() == "true":
            tb = html.escape(traceback.format_exc())
            return Response(f"<pre>{tb}</pre>", 500)
        return Response("<h1>500 Internal Server Error</h1>", 500)
