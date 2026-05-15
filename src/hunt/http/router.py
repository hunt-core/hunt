from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from hunt.http.route import Route


class RouteNotFoundException(Exception):
    pass


class Router:
    def __init__(self) -> None:
        self._routes: list[Route] = []
        self._named: dict[str, Route] = {}
        self._group_prefix: str = ""
        self._group_middleware: list[Any] = []

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def get(self, uri: str, action: Callable) -> Route:
        return self._add(["GET", "HEAD"], uri, action)

    def post(self, uri: str, action: Callable) -> Route:
        return self._add(["POST"], uri, action)

    def put(self, uri: str, action: Callable) -> Route:
        return self._add(["PUT"], uri, action)

    def patch(self, uri: str, action: Callable) -> Route:
        return self._add(["PATCH"], uri, action)

    def delete(self, uri: str, action: Callable) -> Route:
        return self._add(["DELETE"], uri, action)

    def any(self, uri: str, action: Callable) -> Route:
        return self._add(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], uri, action)

    def match(self, methods: list[str], uri: str, action: Callable) -> Route:
        return self._add(methods, uri, action)

    def resource(self, prefix: str, controller: Any) -> None:
        """Register standard resource routes for a controller class."""
        self.get(f"/{prefix}", lambda req: controller().index(req)).named(f"{prefix}.index")
        self.get(f"/{prefix}/create", lambda req: controller().create(req)).named(f"{prefix}.create")
        self.post(f"/{prefix}", lambda req: controller().store(req)).named(f"{prefix}.store")
        self.get(f"/{prefix}/{{id}}", lambda req, id: controller().show(req, id)).named(f"{prefix}.show")
        self.get(f"/{prefix}/{{id}}/edit", lambda req, id: controller().edit(req, id)).named(f"{prefix}.edit")
        self.put(f"/{prefix}/{{id}}", lambda req, id: controller().update(req, id)).named(f"{prefix}.update")
        self.patch(f"/{prefix}/{{id}}", lambda req, id: controller().update(req, id))
        self.delete(f"/{prefix}/{{id}}", lambda req, id: controller().destroy(req, id)).named(f"{prefix}.destroy")

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    @contextmanager
    def group(
        self,
        prefix: str = "",
        middleware: list[Any] | None = None,
    ) -> Generator[None, None, None]:
        old_prefix = self._group_prefix
        old_mw = self._group_middleware[:]
        self._group_prefix = old_prefix + prefix
        self._group_middleware = old_mw + (middleware or [])
        try:
            yield
        finally:
            self._group_prefix = old_prefix
            self._group_middleware = old_mw

    def prefix(self, prefix: str) -> _PrefixGroup:
        return _PrefixGroup(self, prefix)

    def middleware(self, *mw: Any) -> _MiddlewareGroup:
        return _MiddlewareGroup(self, list(mw))

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, method: str, path: str) -> tuple[Route, dict[str, str]]:
        # Normalize trailing slash — /foo/ and /foo are the same route.
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        for route in self._routes:
            matched, params = route.matches(method, path)
            if matched:
                return route, params
        raise RouteNotFoundException(f"No route found for {method} {path}")

    def url(self, name: str, params: dict[str, Any] | None = None) -> str:
        if name not in self._named:
            raise KeyError(f"No named route '{name}'")
        return self._named[name].url(params)

    def routes(self) -> list[Route]:
        return list(self._routes)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add(self, methods: list[str], uri: str, action: Callable) -> Route:
        full_uri = self._group_prefix + ("" if uri == "/" and self._group_prefix else uri)
        if not full_uri:
            full_uri = "/"
        route = Route(methods, full_uri, action, middleware=list(self._group_middleware))
        self._routes.append(route)
        return route

    def _register_named(self, route: Route) -> None:
        if route.name:
            self._named[route.name] = route


class _PrefixGroup:
    def __init__(self, router: Router, prefix: str) -> None:
        self._router = router
        self._prefix = prefix

    def group(self, callback: Callable[[], None]) -> None:
        with self._router.group(prefix=self._prefix):
            callback()


class _MiddlewareGroup:
    def __init__(self, router: Router, middleware: list[Any]) -> None:
        self._router = router
        self._middleware = middleware

    def group(self, callback: Callable[[], None]) -> None:
        with self._router.group(middleware=self._middleware):
            callback()
