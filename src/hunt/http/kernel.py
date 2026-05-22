from __future__ import annotations

import inspect
import mimetypes
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse, RedirectResponse, Response
from hunt.http.router import RouteNotFoundException, Router
from hunt.validation.validator import ValidationException

_BLOCKED_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyc",
        ".pyo",
        ".pyd",
        ".pyw",
        ".env",
        ".sh",
        ".bash",
        ".cfg",
        ".ini",
        ".toml",
        ".yaml",
        ".yml",
        ".htaccess",
        ".htpasswd",
        ".phar",
        ".phtml",
        ".php",
        ".svg",  # SVG can contain inline <script> — serve as download if needed
    }
)

_SENSITIVE_FLASH_KEYS = frozenset(
    {
        "password",
        "password_confirmation",
        "current_password",
        "new_password",
    }
)


class HttpKernel:
    """ASGI application entry point."""

    def __init__(
        self,
        router: Router,
        global_middleware: list[Any] | None = None,
        exception_handler: Any | None = None,
    ) -> None:
        self._router = router
        self._global_middleware: list[Any] = list(global_middleware or [])
        self._exception_handler = exception_handler

        if os.environ.get("APP_ENV", "local") != "testing":
            from hunt.http.middleware.request_id import RequestId

            if not any(m is RequestId or (isinstance(m, type) and issubclass(m, RequestId)) for m in self._global_middleware):
                self._global_middleware.insert(0, RequestId)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope["type"] != "http":
            return

        if await self._try_static(scope.get("path", "/"), send):
            return

        if scope.get("path") == "/health" and os.environ.get("HEALTH_CHECK_ENABLED", "true").lower() != "false":
            from hunt import __version__

            resp = JsonResponse({"status": "ok", "version": __version__})
            await resp(scope, receive, send)
            return

        body = await self._read_body(receive)
        request = Request(scope, body)

        start = time.monotonic()
        resp = await self._handle(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        from hunt.log.manager import Log

        Log.info(
            f"{request.method} {request.path} {resp.status}",
            ms=f"{elapsed_ms:.1f}",
        )

        await resp(scope, receive, send)

    async def _handle(self, request: Request) -> Response:
        from hunt.auth.manager import _set_request
        from hunt.database.debug import reset_query_tracker

        _set_request(request)
        reset_query_tracker()

        try:
            route, params = self._router.dispatch(request.method, request.path, host=request.host)
        except RouteNotFoundException:
            if request.method == "OPTIONS":
                return await self._handle_options(request)
            allowed = self._router.allowed_methods(request.path)
            if allowed:
                allow_value = ", ".join(sorted({*allowed, "OPTIONS"}))
                resp = self._render_error(request, HttpException(405, "Method Not Allowed"))
                resp.header("Allow", allow_value)
                return resp
            return self._render_error(request, HttpException(404, "Not Found"))

        request.set_route_params(params)
        request._debug_route_name = route.name  # type: ignore[attr-defined]
        request._debug_route_uri = route.uri  # type: ignore[attr-defined]

        all_middleware = [*self._global_middleware, *route._middleware]
        handler = self._make_handler(route.action, params)
        pipeline = self._build_pipeline(all_middleware, handler)

        try:
            return await pipeline(request)
        except HttpException as e:
            return self._render_error(request, e)
        except ValidationException as e:
            return self._render_validation_error(request, e)
        except Exception as e:
            return self._render_exception(request, e)

    async def _handle_options(self, request: Request) -> Response:
        """Return an implicit 204 for OPTIONS preflight — runs global middleware (e.g. CORS)."""
        allowed = self._router.allowed_methods(request.path)
        if not allowed:
            return self._render_error(request, HttpException(404, "Not Found"))
        allow_value = ", ".join(sorted({*allowed, "OPTIONS"}))

        async def _options_action(req: Request) -> Response:
            return Response("", 204, {"Allow": allow_value})

        pipeline = self._build_pipeline(self._global_middleware, _options_action)
        try:
            return await pipeline(request)
        except Exception:
            return Response("", 204, {"Allow": allow_value})

    def _make_handler(self, action: Callable, params: dict[str, str]) -> Next:
        async def handler(request: Request) -> Response:
            from hunt.database.model import Model
            from hunt.validation.form_request import FormRequest

            sig = inspect.signature(action)
            kwargs: dict[str, Any] = {}
            for name, param in sig.parameters.items():
                if name in ("request", "req"):
                    kwargs[name] = request
                elif name in params:
                    ann = param.annotation
                    if ann is not inspect.Parameter.empty and isinstance(ann, type) and issubclass(ann, Model):
                        kwargs[name] = ann.find_or_fail(params[name])
                    else:
                        kwargs[name] = params[name]
                elif param.default is not inspect.Parameter.empty:
                    kwargs[name] = param.default
                elif param.annotation is not inspect.Parameter.empty:
                    ann = param.annotation
                    if isinstance(ann, type) and issubclass(ann, FormRequest):
                        form_req = ann(request)
                        form_req.validated()  # raises ValidationException on failure
                        kwargs[name] = form_req

            result = action(**kwargs)
            if inspect.iscoroutine(result):
                result = await result

            if isinstance(result, Response):
                return result
            from hunt.http.resources import ApiResource, ApiResourceCollection

            if isinstance(result, (ApiResource, ApiResourceCollection)):
                return result.to_response(request)
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

    @staticmethod
    async def _try_static(path: str, send: Any) -> bool:
        """Serve a file from public/ or storage/app/public/ if one exists at the path."""
        cwd = Path.cwd()
        file_path = await HttpKernel._resolve_static(path, cwd)
        if file_path is None:
            return False

        data = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = (content_type or "application/octet-stream").encode()

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", content_type),
                    (b"content-length", str(len(data)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": data})
        return True

    @staticmethod
    async def _resolve_static(path: str, cwd: Path) -> Path | None:
        """Return the Path to serve for the given URL path, or None."""
        candidates: list[tuple[Path, str]] = [
            (cwd / "public", path.lstrip("/") or "index.html"),
            (cwd / "storage" / "app" / "public", path[len("/storage/") :] if path.startswith("/storage/") else ""),
        ]
        for root, rel in candidates:
            if not rel or not root.is_dir():
                continue
            try:
                resolved = (root / rel).resolve()
                resolved.relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            if resolved.suffix.lower() in _BLOCKED_EXTENSIONS:
                continue
            if resolved.is_file():
                return resolved
        return None

    def _render_validation_error(self, request: Request, exc: ValidationException) -> Response:
        from hunt.log.manager import Log

        errors_flat = "; ".join(exc.all())
        Log.warning(
            f"Validation failed {request.method} {request.path}",
            errors=errors_flat,
        )
        if request.expects_json():
            return JsonResponse({"errors": exc.errors}, 422)
        store = getattr(request, "_session", None)
        if store is not None:
            store.flash("_errors", exc.errors)
            store.flash(
                "_old_input",
                {
                    k: v
                    for k, v in request.all().items()
                    if not hasattr(v, "content") and k not in _SENSITIVE_FLASH_KEYS
                },
            )
        back = request.header("referer") or "/"
        from hunt.http.response import _is_safe_redirect

        if not _is_safe_redirect(back):
            back = "/"
        return RedirectResponse(back, 302)

    def _render_error(self, request: Request, exc: HttpException) -> Response:
        if self._exception_handler:
            return self._exception_handler.render(request, exc)
        if request.expects_json():
            return JsonResponse({"error": exc.message or "Error"}, exc.status)
        import html

        safe_msg = html.escape(exc.message or "")
        return Response(f"<h1>{exc.status}</h1><p>{safe_msg}</p>", exc.status)

    def _render_exception(self, request: Request, exc: Exception) -> Response:
        from hunt.log.manager import Log

        Log.exception(f"Unhandled exception on {request.method} {request.path}", exc=exc)

        try:
            from hunt.container.facade import _app

            if _app is not None:
                for hook in getattr(_app, "_error_handlers", []):
                    try:
                        hook(exc, request)
                    except Exception:
                        pass
        except Exception:
            pass

        if self._exception_handler:
            return self._exception_handler.render(request, exc)
        import html
        import os
        import traceback

        if os.environ.get("APP_DEBUG", "false").lower() == "true":
            tb = html.escape(traceback.format_exc())
            return Response(f"<pre>{tb}</pre>", 500)
        return Response("<h1>500 Internal Server Error</h1>", 500)
