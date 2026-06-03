from __future__ import annotations

import inspect
import mimetypes
import os
import time
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse, RedirectResponse, Response
from hunt.http.router import RouteNotFoundException, Router
from hunt.validation.validator import ValidationException


def parsedate_to_datetime_safe(value: str) -> float | None:
    """Parse an HTTP-date header into a POSIX timestamp, or None if malformed."""
    try:
        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None

# Static files are served from an *allowlist* of safe web asset extensions —
# anything not listed falls through to routing (and a 404) rather than being
# served from disk. This is safer than a denylist: new/unknown extensions are
# refused by default. Override with the STATIC_EXTENSIONS env var (comma list,
# leading dots optional). SVG is intentionally excluded (can carry inline
# <script>); add it explicitly if you trust your asset pipeline.
_DEFAULT_STATIC_EXTENSIONS = frozenset(
    {
        ".html", ".htm", ".css", ".js", ".mjs", ".map", ".json", ".txt",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".bmp",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".pdf", ".mp4", ".webm", ".mp3", ".ogg", ".wav",
        ".xml", ".webmanifest", ".csv",
    }
)


def _static_extensions() -> frozenset[str]:
    raw = os.environ.get("STATIC_EXTENSIONS", "").strip()
    if not raw:
        return _DEFAULT_STATIC_EXTENSIONS
    exts = {("." + e.strip().lstrip(".")).lower() for e in raw.split(",") if e.strip()}
    return frozenset(exts) or _DEFAULT_STATIC_EXTENSIONS

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
        app: Any | None = None,
    ) -> None:
        self._router = router
        self._global_middleware: list[Any] = list(global_middleware or [])
        self._exception_handler = exception_handler
        self._app = app
        self._offload_sync = os.environ.get("OFFLOAD_SYNC_HANDLERS", "false").lower() == "true"

        if os.environ.get("APP_ENV", "local") != "testing":
            from hunt.http.middleware.request_id import RequestId

            if not any(
                m is RequestId or (isinstance(m, type) and issubclass(m, RequestId)) for m in self._global_middleware
            ):
                self._global_middleware.insert(0, RequestId)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope["type"] != "http":
            return

        if await self._try_static(scope, send):
            return

        if scope.get("path") == "/health" and os.environ.get("HEALTH_CHECK_ENABLED", "true").lower() != "false":
            # Don't leak the framework version to anonymous probes by default;
            # opt in with HEALTH_CHECK_VERBOSE=true for internal monitoring.
            payload = {"status": "ok"}
            if os.environ.get("HEALTH_CHECK_VERBOSE", "false").lower() == "true":
                from hunt import __version__

                payload["version"] = __version__
            resp = JsonResponse(payload)
            await resp(scope, receive, send)
            return

        body = await self._read_body(receive)
        request = Request(scope, body)

        start = time.monotonic()
        resp = await self._handle(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        # Per-request access logging is opt-out: disable with ACCESS_LOG=false when
        # a reverse proxy already records access logs. The write itself is routed
        # off the event loop by the log manager (see _maybe_non_blocking).
        if os.environ.get("ACCESS_LOG", "true").lower() != "false":
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
        handler = self._make_handler(route, params)
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

    @staticmethod
    def _binding_plan(route: Any, params: dict[str, str]) -> list[tuple[str, str, Any]]:
        """Resolve how each handler parameter is supplied, computed once per route.

        ``inspect.signature`` and the annotation checks are reflective and were
        previously run on every request; the result is stable for a given route
        (its URL params don't change), so we cache the plan on the route object.
        Each entry is ``(name, kind, extra)``.
        """
        plan = getattr(route, "_binding_plan", None)
        if plan is not None:
            return plan

        from hunt.database.model import Model
        from hunt.validation.form_request import FormRequest

        plan = []
        for name, param in inspect.signature(route.action).parameters.items():
            if name in ("request", "req"):
                plan.append((name, "request", None))
            elif name in params:
                ann = param.annotation
                if ann is not inspect.Parameter.empty and isinstance(ann, type) and issubclass(ann, Model):
                    plan.append((name, "route_model", ann))
                else:
                    plan.append((name, "route_str", None))
            elif param.default is not inspect.Parameter.empty:
                plan.append((name, "default", param.default))
            elif param.annotation is not inspect.Parameter.empty:
                ann = param.annotation
                if isinstance(ann, type) and issubclass(ann, FormRequest):
                    plan.append((name, "form_request", ann))
        route._binding_plan = plan
        return plan

    def _make_handler(self, route: Any, params: dict[str, str]) -> Next:
        action = route.action
        plan = self._binding_plan(route, params)
        # A synchronous handler that does blocking DB/IO stalls the whole event
        # loop while it runs. When OFFLOAD_SYNC_HANDLERS=true, run such handlers
        # in a worker thread (asyncio.to_thread copies the contextvars the request
        # context relies on) so other requests keep being served. Async handlers
        # are never offloaded — they already yield to the loop.
        offload = self._offload_sync and not inspect.iscoroutinefunction(action)

        async def handler(request: Request) -> Response:
            kwargs: dict[str, Any] = {}
            for name, kind, extra in plan:
                if kind == "request":
                    kwargs[name] = request
                elif kind == "route_str":
                    kwargs[name] = params[name]
                elif kind == "route_model":
                    try:
                        kwargs[name] = extra.resolve_route_binding(params[name])
                    except ValueError as exc:
                        from hunt.http.exceptions import HttpException

                        raise HttpException(404, "Not Found.") from exc
                elif kind == "default":
                    kwargs[name] = extra
                elif kind == "form_request":
                    form_req = extra(request)
                    form_req.validated()  # raises ValidationException on failure
                    kwargs[name] = form_req

            if offload:
                import asyncio

                result = await asyncio.to_thread(action, **kwargs)
            else:
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

    _DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB

    @classmethod
    def _max_body_bytes(cls) -> int:
        raw = os.environ.get("MAX_BODY_SIZE", "")
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
        return cls._DEFAULT_MAX_BODY_BYTES

    @classmethod
    async def _read_body(cls, receive: Any) -> bytes:
        limit = cls._max_body_bytes()
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > limit:
                raise HttpException(413, "Request body too large.")
            if not message.get("more_body", False):
                break
        return body

    async def _handle_lifespan(self, scope: dict, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await self._run_hooks("_startup_handlers")
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self._run_hooks("_shutdown_handlers")
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _run_hooks(self, attr: str) -> None:
        handlers = getattr(self._app, attr, []) if self._app is not None else []
        for handler in handlers:
            try:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                import logging

                logging.getLogger("hunt").error("Lifespan hook %r raised: %s", handler, exc)

    def _static_roots(self) -> list[tuple[str, Path]]:
        """Existing static roots, resolved once and cached for the worker's lifetime.

        Each entry is ``(kind, resolved_path)`` where kind is ``"public"`` or
        ``"storage"`` — both directories are named ``public`` on disk, so the kind
        tag is what distinguishes how a URL path maps onto them.
        """
        cached = getattr(self, "_static_roots_cache", None)
        if cached is None:
            cwd = Path.cwd()
            candidates = [("public", cwd / "public"), ("storage", cwd / "storage" / "app" / "public")]
            cached = [(kind, p.resolve()) for kind, p in candidates if p.is_dir()]
            self._static_roots_cache = cached  # type: ignore[attr-defined]
        return cached

    async def _try_static(self, scope: dict, send: Any) -> bool:
        """Serve a file from public/ or storage/app/public/ if one exists at the path.

        Adds Cache-Control/ETag/Last-Modified and answers conditional requests
        (If-None-Match / If-Modified-Since) with 304 — without reading the body.
        """
        path = scope.get("path", "/")
        # Cheap pre-filter: only "/" or paths with an allowed extension can ever
        # match a static asset, so dynamic routes skip all filesystem syscalls.
        allowed = _static_extensions()
        if path != "/" and os.path.splitext(path)[1].lower() not in allowed:
            return False

        roots = self._static_roots()
        if not roots:
            return False

        file_path = self._resolve_static(path, roots, allowed)
        if file_path is None:
            return False

        try:
            stat = file_path.stat()
        except OSError:
            return False

        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        cache_control = os.environ.get("STATIC_CACHE_CONTROL", "public, max-age=3600")

        base_headers: list[tuple[bytes, bytes]] = [
            (b"etag", etag.encode()),
            (b"last-modified", last_modified.encode()),
            (b"cache-control", cache_control.encode()),
        ]

        if self._static_not_modified(scope, etag, stat.st_mtime):
            await send({"type": "http.response.start", "status": 304, "headers": base_headers})
            await send({"type": "http.response.body", "body": b""})
            return True

        data = file_path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(file_path))
        headers = [
            (b"content-type", (content_type or "application/octet-stream").encode()),
            (b"content-length", str(len(data)).encode()),
            *base_headers,
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": data})
        return True

    @staticmethod
    def _static_not_modified(scope: dict, etag: str, mtime: float) -> bool:
        """Return True if the client's cached copy is still fresh."""
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        inm = headers.get(b"if-none-match")
        if inm is not None:
            return etag.encode() in {t.strip() for t in inm.split(b",")}
        ims = headers.get(b"if-modified-since")
        if ims is not None:
            parsed = parsedate_to_datetime_safe(ims.decode("latin-1"))
            if parsed is not None:
                return int(mtime) <= int(parsed)
        return False

    @staticmethod
    def _resolve_static(path: str, roots: list[tuple[str, Path]], allowed: frozenset[str]) -> Path | None:
        """Return the Path to serve for the given URL path, or None."""
        for kind, root in roots:
            if kind == "public":
                rel = path.lstrip("/") or "index.html"
            elif path.startswith("/storage/"):
                rel = path[len("/storage/") :]
            else:
                continue
            if not rel:
                continue
            try:
                resolved = (root / rel).resolve()
                resolved.relative_to(root)
            except (ValueError, OSError):
                continue
            if resolved.suffix.lower() not in allowed:
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
