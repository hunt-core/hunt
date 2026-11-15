from __future__ import annotations

import asyncio
import functools
import inspect
import mimetypes
import os
import time
import typing
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any

from hunt.http.middleware import Middleware, Next
from hunt.http.request import _SENSITIVE_FLASH_KEYS, ClientDisconnected, Request, default_max_body_bytes
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
        ".html",
        ".htm",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".json",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
        ".ico",
        ".bmp",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".pdf",
        ".mp4",
        ".webm",
        ".mp3",
        ".ogg",
        ".wav",
        ".xml",
        ".webmanifest",
        ".csv",
    }
)


def _static_extensions() -> frozenset[str]:
    raw = os.environ.get("STATIC_EXTENSIONS", "").strip()
    if not raw:
        return _DEFAULT_STATIC_EXTENSIONS
    exts = {("." + e.strip().lstrip(".")).lower() for e in raw.split(",") if e.strip()}
    return frozenset(exts) or _DEFAULT_STATIC_EXTENSIONS


# Types a browser will execute or render as a document. Under the public
# *storage* root (user uploads, same origin as the app) they are never served
# with their real type: see HttpKernel._static_headers.
_ACTIVE_STATIC_EXTENSIONS = frozenset({".html", ".htm", ".xhtml", ".xml", ".svg", ".js", ".mjs"})

_STATIC_CHUNK_SIZE = 64 * 1024


# Kept under its old private name; the exception itself lives with the request.
_ClientDisconnected = ClientDisconnected

_FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


async def _load_body(request: Any) -> None:
    """Read the request body if it has not been read yet."""
    loader = getattr(request, "load_body", None)
    if loader is not None:
        await loader()


def _abandon_body(request: Any) -> None:
    abandon = getattr(request, "_abandon_body", None)
    if abandon is not None:
        abandon()


def _read_file_range(path: Path, start: int, length: int) -> bytes:
    with open(path, "rb") as fh:
        if start:
            fh.seek(start)
        return fh.read(length)


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

        from hunt.application import app_env

        if app_env() != "testing":
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
            # Only HTTP is handled. A WebSocket handshake must still be answered,
            # or the client hangs until it times out: closing before accepting
            # makes the server reply 403.
            if scope["type"] == "websocket":
                try:
                    await receive()
                    await send({"type": "websocket.close", "code": 1008})
                except Exception:
                    pass
            return

        if scope.get("method", "GET").upper() in ("GET", "HEAD") and await self._try_static(scope, send):
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

        # The body is not read here: the request is routed first, and the body
        # is loaded inside the pipeline, just before the first middleware (or
        # the handler) that may use it. A request refused earlier (404, 405,
        # 413 on Content-Length, 429, 401...) is answered without reading it.
        request = Request(scope, receive=receive)

        start = time.monotonic()
        try:
            resp = await self._handle(request)
        except ClientDisconnected:
            # Nobody is left to answer, and a truncated body must never reach a handler.
            return
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

        # <input type="hidden" name="_method" value="PUT"> on a POST form.
        try:
            await self._apply_method_override(request)
        except HttpException as exc:
            return await self._reject(request, exc)

        try:
            route, params = self._router.dispatch(request.method, request.path, host=request.host)
        except RouteNotFoundException:
            return await self._handle_unmatched(request)

        request.set_route_params(params)
        request.route = route
        request._debug_route_name = route.name  # type: ignore[attr-defined]
        request._debug_route_uri = route.uri  # type: ignore[attr-defined]

        request._body_limit = self._route_body_limit(route)
        if request._exceeds_body_limit():
            # Declared Content-Length (or a body already read for the _method
            # override) over this route's limit: refuse before reading anything.
            return await self._reject(request, HttpException(413, "Request body too large."))

        all_middleware = [*self._global_middleware, *route._middleware]
        handler = self._make_handler(route, params)
        return await self._run_pipeline(all_middleware, handler, request)

    async def _apply_method_override(self, request: Request) -> None:
        """Honour a POST form's ``_method`` field, which has to be known before routing.

        That needs the form, hence the body. It is read this early only when it
        can change the outcome: a POST with a form content type to a path that
        has a PUT, PATCH or DELETE route. Any other request is routed unread.
        """
        if request.body_loaded:
            try:
                request.apply_method_override()
            except Exception:
                pass
            return
        if request.real_method != "POST" or request.is_json():
            return
        content_type = (request.header("content-type", "") or "").lower()
        if not any(form_type in content_type for form_type in _FORM_CONTENT_TYPES):
            return
        targets = self._router.method_override_targets(request.path, request.host)
        if not targets:
            return
        # The route is not known yet: allow the largest limit among the routes
        # this request can end up on. The matched route's own limit is applied
        # once it is known.
        candidates = list(targets)
        try:
            candidates.append(self._router.dispatch("POST", request.path, host=request.host)[0])
        except RouteNotFoundException:
            pass
        request._body_limit = max(self._route_body_limit(route) for route in candidates)
        await request.load_body()
        request._body_limit = None
        try:
            request.apply_method_override()
        except Exception:
            pass

    @classmethod
    def _route_body_limit(cls, route: Any) -> int:
        limit = getattr(route, "_max_body", None)
        return limit if isinstance(limit, int) else cls._max_body_bytes()

    async def _reject(self, request: Request, exc: HttpException) -> Response:
        """Answer with an error raised before the route's pipeline could start.

        It is raised from a terminal action behind the *global* middleware, so
        the response gets the usual treatment (security headers, CORS, request
        ID, session).
        """
        if exc.status == 413:
            headers = dict(getattr(exc, "headers", None) or {})
            # The body will not be read, so the connection cannot be reused.
            headers.setdefault("Connection", "close")
            headers.setdefault("X-Content-Type-Options", "nosniff")
            exc.headers = headers
        # Nothing behind this point may read the body: user middleware that
        # asks for it sees an empty one.
        _abandon_body(request)

        async def _error_action(req: Request) -> Response:
            raise exc

        return await self._run_pipeline(self._global_middleware, _error_action, request)

    async def _run_pipeline(self, middleware_list: list[Any], handler: Next, request: Request) -> Response:
        """Build and run a pipeline. Exceptions are converted to responses
        inside it (see ``_safe``); the handlers here are a last resort."""
        try:
            pipeline = self._build_pipeline(middleware_list, handler)
            return await pipeline(request)
        except ClientDisconnected:
            raise
        except HttpException as e:
            _abandon_body(request)
            return self._render_error(request, e)
        except ValidationException as e:
            _abandon_body(request)
            return self._render_validation_error(request, e)
        except Exception as e:
            _abandon_body(request)
            return self._render_exception(request, e)

    async def _handle_unmatched(self, request: Request) -> Response:
        """404 / 405 / implicit OPTIONS for a request no route matched.

        The error is raised from a terminal action at the end of the *global*
        middleware, so these responses get the same treatment (security
        headers, CORS, request ID, session) as any other.
        """
        if request.method == "OPTIONS":
            return await self._handle_options(request)
        allowed = self._router.allowed_methods(request.path)
        if allowed:
            allow_value = ", ".join(sorted({*allowed, "OPTIONS"}))
            exc = HttpException(405, "Method Not Allowed", headers={"Allow": allow_value})
        else:
            exc = HttpException(404, "Not Found")

        async def _error_action(req: Request) -> Response:
            raise exc

        return await self._run_pipeline(self._global_middleware, _error_action, request)

    async def _handle_options(self, request: Request) -> Response:
        """Return an implicit 204 for an OPTIONS preflight.

        Runs the global middleware plus any ``HandleCors`` attached to the routes
        registered for this path, so route-level CORS can answer the preflight.
        Other route middleware (auth, throttle, CSRF) is not run: a preflight
        carries no credentials and must not be rejected or counted.
        """
        matching = self._router.routes_matching_path(request.path)
        if not matching:

            async def _not_found(req: Request) -> Response:
                raise HttpException(404, "Not Found")

            return await self._run_pipeline(self._global_middleware, _not_found, request)

        methods: set[str] = set()
        for route in matching:
            methods.update(route.methods)
        allow_value = ", ".join(sorted({*methods, "OPTIONS"}))

        from hunt.http.middleware.cors import HandleCors

        cors: list[Any] = []
        for route in matching:
            for mw in route._middleware:
                is_cors = issubclass(mw, HandleCors) if isinstance(mw, type) else isinstance(mw, HandleCors)
                if is_cors and mw not in cors and mw not in self._global_middleware:
                    cors.append(mw)

        async def _options_action(req: Request) -> Response:
            return Response("", 204, {"Allow": allow_value})

        return await self._run_pipeline([*self._global_middleware, *cors], _options_action, request)

    @staticmethod
    def _type_hints(action: Any) -> dict[str, Any]:
        """Resolved annotations of a handler, including string annotations
        (``from __future__ import annotations``).

        Annotations that cannot be resolved (a name imported only under
        ``TYPE_CHECKING``, a class local to another function) are left out.
        """
        target = action
        while isinstance(target, functools.partial):
            target = target.func
        is_plain = inspect.isfunction(target) or inspect.ismethod(target) or isinstance(target, type)
        if not is_plain and callable(target):
            target = type(target).__call__  # a callable object: its __call__ carries the hints
        try:
            return dict(typing.get_type_hints(target))
        except Exception:
            pass

        try:
            fn = inspect.unwrap(getattr(target, "__func__", target))
            raw = dict(getattr(fn, "__annotations__", None) or {})
            globalns = getattr(fn, "__globals__", {})
        except Exception:
            return {}
        hints: dict[str, Any] = {}
        for name, ann in raw.items():
            if not isinstance(ann, str):
                hints[name] = ann
                continue

            def _probe() -> None:  # carries one annotation for get_type_hints
                return None

            _probe.__annotations__ = {name: ann}
            try:
                hints[name] = typing.get_type_hints(_probe, globalns=globalns)[name]
            except Exception:
                continue
        return hints

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

        hints = HttpKernel._type_hints(route.action)
        plan = []
        for name, param in inspect.signature(route.action).parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            ann = hints.get(name, param.annotation)
            is_form_request = isinstance(ann, type) and issubclass(ann, FormRequest)
            if name in ("request", "req") and not is_form_request:
                # A parameter called `request` is the plain Request, unless it
                # is annotated with a FormRequest subclass (`request: StorePost`).
                plan.append((name, "request", None))
            elif name in params:
                if isinstance(ann, type) and issubclass(ann, Model):
                    plan.append((name, "route_model", ann))
                else:
                    plan.append((name, "route_str", None))
            elif isinstance(ann, type) and issubclass(ann, FormRequest):
                plan.append((name, "form_request", ann))
            elif isinstance(ann, type) and issubclass(ann, Request):
                plan.append((name, "request", None))
            elif param.default is not inspect.Parameter.empty:
                plan.append((name, "default", param.default))
            else:
                shown = ann if isinstance(ann, str) else getattr(ann, "__name__", None)
                detail = f" (annotation {shown!r} could not be resolved to a FormRequest)" if shown else ""
                raise TypeError(
                    f"Cannot bind parameter '{name}' of the handler for route '{route.uri}'{detail}. "
                    "Handler parameters must be 'request', a route parameter, a FormRequest subclass, "
                    "or have a default value."
                )
        route._binding_plan = plan
        return plan

    def _make_handler(self, route: Any, params: dict[str, str]) -> Next:
        action = route.action
        # A synchronous handler that does blocking DB/IO stalls the whole event
        # loop while it runs. When OFFLOAD_SYNC_HANDLERS=true, run such handlers
        # in a worker thread (asyncio.to_thread copies the contextvars the request
        # context relies on) so other requests keep being served. Async handlers
        # are never offloaded — they already yield to the loop.
        offload = self._offload_sync and not inspect.iscoroutinefunction(action)
        stream_body = bool(getattr(route, "_stream_body", False))

        async def handler(request: Request) -> Response:
            if not stream_body:
                # Parameter binding (FormRequest validation), a handler running
                # in a worker thread and every synchronous accessor need the
                # whole body: read it here, on the event loop.
                await _load_body(request)
            # Resolved here, inside the pipeline, so a handler that cannot be
            # bound is reported through the normal error path.
            plan = self._binding_plan(route, params)
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
                        raise HttpException(404, "Not Found.") from exc
                elif kind == "default":
                    kwargs[name] = extra
                elif kind == "form_request":
                    form_req = extra(request)
                    form_req.validated()  # raises ValidationException on failure
                    kwargs[name] = form_req

            if offload:
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

    def _safe(self, nxt: Next) -> Next:
        """Wrap a pipeline stage so it always returns a Response.

        Without this an exception unwinds straight through every outer
        middleware: the session is not saved (flashed validation errors and a
        regenerated ID are lost) and the error response gets no security, CORS,
        request-ID or rate-limit headers.
        """

        async def safe(request: Request) -> Response:
            try:
                return await nxt(request)
            except ClientDisconnected:
                # Not an error to render: the kernel drops the request.
                raise
            except HttpException as e:
                _abandon_body(request)
                return self._render_error(request, e)
            except ValidationException as e:
                _abandon_body(request)
                return self._render_validation_error(request, e)
            except Exception as e:
                _abandon_body(request)
                return self._render_exception(request, e)

        return safe

    def _build_pipeline(self, middleware_list: list[Any], handler: Next) -> Next:
        pipeline = self._safe(handler)
        for mw in reversed(middleware_list):
            instance = mw() if isinstance(mw, type) else mw
            current_next = pipeline

            def make_layer(m: Middleware, nxt: Next) -> Next:
                async def layer(request: Request) -> Response:
                    # A middleware that may use request input gets the body
                    # loaded first; one that declares ``reads_body = False``
                    # runs (and may reject the request) before it is read.
                    if getattr(m, "reads_body", True):
                        await _load_body(request)
                    return await m.handle(request, nxt)

                return layer

            # Each layer is wrapped too: an exception raised *by* middleware
            # (CSRF 419, throttle 429, auth 401) becomes a response right there,
            # so the middleware outside it still post-processes that response.
            pipeline = self._safe(make_layer(instance, current_next))
        return pipeline

    _DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB

    @classmethod
    def _max_body_bytes(cls) -> int:
        return default_max_body_bytes(cls._DEFAULT_MAX_BODY_BYTES)

    @classmethod
    async def _read_body(cls, receive: Any, scope: dict | None = None) -> bytes:
        """Read a whole request body (kept for callers of the old eager API).

        Raises ``HttpException(413)`` when it exceeds MAX_BODY_SIZE and
        ``ClientDisconnected`` when the client goes away mid-body.
        """
        request = Request(scope if scope is not None else {}, receive=receive)
        request._body_limit = cls._max_body_bytes()
        return await request.load_body()

    async def _handle_lifespan(self, scope: dict, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                errors = await self._run_hooks("_startup_handlers", stop_on_error=True)
                if errors:
                    # Tell the server startup failed so it exits instead of
                    # serving traffic from a half-initialised application.
                    await send({"type": "lifespan.startup.failed", "message": errors[0]})
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                errors = await self._run_hooks("_shutdown_handlers")
                if errors:
                    await send({"type": "lifespan.shutdown.failed", "message": "; ".join(errors)})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                return

    async def _run_hooks(self, attr: str, stop_on_error: bool = False) -> list[str]:
        """Run lifespan hooks; return a message for each one that raised."""
        handlers = getattr(self._app, attr, []) if self._app is not None else []
        errors: list[str] = []
        for handler in handlers:
            try:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                import logging

                logging.getLogger("hunt").error("Lifespan hook %r raised: %s", handler, exc)
                errors.append(f"Lifespan hook {getattr(handler, '__name__', handler)!r} raised: {exc}")
                if stop_on_error:
                    break
        return errors

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

        Only GET and HEAD are served. Adds Cache-Control/ETag/Last-Modified,
        answers conditional requests with 304 and single ``Range`` requests with
        206. The file is read in chunks on a worker thread, never on the event
        loop, and never whole into memory.
        """
        method = scope.get("method", "GET").upper()
        if method not in ("GET", "HEAD"):
            return False
        path = scope.get("path", "/")
        # Cheap pre-filter: only "/" or paths with an allowed extension can ever
        # match a static asset, so dynamic routes skip all filesystem syscalls.
        allowed = _static_extensions()
        if path != "/" and os.path.splitext(path)[1].lower() not in allowed:
            return False

        roots = self._static_roots()
        if not roots:
            return False

        located = await asyncio.to_thread(self._locate_static, path, roots, allowed)
        if located is None:
            return False
        kind, file_path, stat = located

        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        base_headers = self._static_headers(kind, file_path, etag, stat.st_mtime)

        if self._static_not_modified(scope, etag, stat.st_mtime):
            await send({"type": "http.response.start", "status": 304, "headers": base_headers})
            await send({"type": "http.response.body", "body": b""})
            return True

        size = stat.st_size
        status = 200
        start, length = 0, size
        byte_range = self._static_range(scope, etag, size)
        if byte_range == "unsatisfiable":
            headers = [(b"content-range", f"bytes */{size}".encode()), (b"content-length", b"0"), *base_headers]
            await send({"type": "http.response.start", "status": 416, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return True
        extra: list[tuple[bytes, bytes]] = []
        if byte_range is not None:
            start, end = byte_range
            length = end - start + 1
            status = 206
            extra.append((b"content-range", f"bytes {start}-{end}/{size}".encode()))

        content_type = self._static_content_type(kind, file_path)
        headers = [
            (b"content-type", content_type.encode()),
            (b"content-length", str(length).encode()),
            (b"accept-ranges", b"bytes"),
            *extra,
            *base_headers,
        ]

        if method == "HEAD" or length == 0:
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": b""})
            return True

        if length <= _STATIC_CHUNK_SIZE:
            try:
                data = await asyncio.to_thread(_read_file_range, file_path, start, length)
            except OSError:
                return False
            if len(data) != length:  # changed on disk since stat(): keep the framing honest
                headers = [(k, str(len(data)).encode() if k == b"content-length" else v) for k, v in headers]
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": data})
            return True

        try:
            fh = await asyncio.to_thread(open, file_path, "rb")
        except OSError:
            return False
        try:
            if start:
                await asyncio.to_thread(fh.seek, start)
            await send({"type": "http.response.start", "status": status, "headers": headers})
            remaining = length
            while remaining > 0:
                chunk = await asyncio.to_thread(fh.read, min(_STATIC_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": remaining > 0})
            if remaining > 0:
                # The file shrank mid-response; end the body rather than hang.
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await asyncio.to_thread(fh.close)
        return True

    def _locate_static(
        self, path: str, roots: list[tuple[str, Path]], allowed: frozenset[str]
    ) -> tuple[str, Path, os.stat_result] | None:
        """Blocking part of static lookup (resolve + stat); run on a worker thread."""
        found = self._resolve_static_kind(path, roots, allowed)
        if found is None:
            return None
        kind, file_path = found
        try:
            return kind, file_path, file_path.stat()
        except OSError:
            return None

    @staticmethod
    def _is_active_upload(kind: str, file_path: Path) -> bool:
        return kind == "storage" and file_path.suffix.lower() in _ACTIVE_STATIC_EXTENSIONS

    @classmethod
    def _static_content_type(cls, kind: str, file_path: Path) -> str:
        if cls._is_active_upload(kind, file_path):
            # With nosniff, a non-script, non-document type cannot be executed
            # or rendered, whatever the bytes are.
            return "application/octet-stream"
        content_type, _ = mimetypes.guess_type(str(file_path))
        return content_type or "application/octet-stream"

    @classmethod
    def _static_headers(cls, kind: str, file_path: Path, etag: str, mtime: float) -> list[tuple[bytes, bytes]]:
        cache_control = os.environ.get("STATIC_CACHE_CONTROL", "public, max-age=3600")
        headers: list[tuple[bytes, bytes]] = [
            (b"etag", etag.encode()),
            (b"last-modified", formatdate(mtime, usegmt=True).encode()),
            (b"cache-control", cache_control.encode("latin-1", "replace")),
            # Static responses bypass the middleware stack, so set this here.
            (b"x-content-type-options", b"nosniff"),
        ]
        if cls._is_active_upload(kind, file_path):
            # storage/app/public holds user uploads served from the app's own
            # origin. HTML/JS/XML/SVG there is stored XSS waiting to happen:
            # force a download and sandbox it in case it is rendered anyway.
            headers.append((b"content-disposition", b"attachment"))
            headers.append((b"content-security-policy", b"sandbox; default-src 'none'"))
        return headers

    @staticmethod
    def _static_range(scope: dict, etag: str, size: int) -> tuple[int, int] | str | None:
        """Parse a single-range ``Range`` header.

        Returns ``(start, end)`` inclusive, ``"unsatisfiable"``, or None when
        there is no usable range (absent, malformed, multi-range, or If-Range
        names a different version): the whole file is then served.
        """
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw = headers.get(b"range")
        if raw is None:
            return None
        if_range = headers.get(b"if-range")
        if if_range is not None and if_range.strip() != etag.encode():
            return None
        value = raw.decode("latin-1").strip()
        if not value.lower().startswith("bytes=") or "," in value:
            return None
        first, sep, last = value[6:].strip().partition("-")
        first, last = first.strip(), last.strip()
        if not sep or (first and not first.isdigit()) or (last and not last.isdigit()):
            return None
        if not first:
            if not last:
                return None
            suffix = int(last)
            if suffix == 0 or size == 0:
                return "unsatisfiable"
            return max(0, size - suffix), size - 1
        start = int(first)
        if start >= size:
            return "unsatisfiable"
        end = min(int(last), size - 1) if last else size - 1
        if end < start:
            return None
        return start, end

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
        found = HttpKernel._resolve_static_kind(path, roots, allowed)
        return found[1] if found is not None else None

    @staticmethod
    def _resolve_static_kind(
        path: str, roots: list[tuple[str, Path]], allowed: frozenset[str]
    ) -> tuple[str, Path] | None:
        """Return ``(root kind, Path)`` to serve for the given URL path, or None."""
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
                return kind, resolved
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
        # Back to the form: the same-origin Referer (browsers send it as an
        # absolute URL), else the last page StartSession recorded, else "/".
        from hunt.http.response import _previous_url

        return RedirectResponse(_previous_url(request, "/"), 302)

    def _render_error(self, request: Request, exc: HttpException) -> Response:
        resp: Response | None = None
        if self._exception_handler:
            try:
                resp = self._exception_handler.render(request, exc)
            except Exception as render_exc:
                from hunt.log.manager import Log

                Log.exception("Exception handler failed while rendering an HTTP error", exc=render_exc)
        if resp is None:
            if request.expects_json():
                resp = JsonResponse({"error": exc.message or "Error"}, exc.status)
            else:
                import html

                safe_msg = html.escape(exc.message or "")
                resp = Response(f"<h1>{exc.status}</h1><p>{safe_msg}</p>", exc.status)
        for key, value in (getattr(exc, "headers", None) or {}).items():
            resp.header(key, str(value))
        return resp

    def _render_exception(self, request: Request, exc: Exception) -> Response:
        from hunt.log.manager import Log
        from hunt.support.helpers import DumpAndDie

        if isinstance(exc, DumpAndDie) and os.environ.get("APP_DEBUG", "false").lower() == "true":
            # dd() in a handler: show what was dumped instead of an error page.
            import html
            import pprint

            dumped = "\n\n".join(html.escape(pprint.pformat(value)) for value in exc.values)
            return Response(f"<pre>{dumped}</pre>", 500)

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
            try:
                return self._exception_handler.render(request, exc)
            except Exception as render_exc:
                Log.exception("Exception handler failed while rendering an exception", exc=render_exc)
        import html
        import traceback

        if os.environ.get("APP_DEBUG", "false").lower() == "true":
            tb = html.escape("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            return Response(f"<pre>{tb}</pre>", 500)
        return Response("<h1>500 Internal Server Error</h1>", 500)
