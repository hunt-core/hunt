"""Tests for hunt.admin.controllers.route_explorer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hunt.admin.application import Admin
from hunt.admin.controllers.route_explorer import _middleware_name

# ---------------------------------------------------------------------------
# _middleware_name
# ---------------------------------------------------------------------------


class TestMiddlewareName:
    def test_class_returns_class_name(self):
        class MyMiddleware:
            pass

        assert _middleware_name(MyMiddleware) == "MyMiddleware"

    def test_instance_returns_class_name(self):
        class AuthMiddleware:
            pass

        assert _middleware_name(AuthMiddleware()) == "AuthMiddleware"

    def test_builtin_class(self):
        assert _middleware_name(str) == "str"

    def test_instance_of_builtin(self):
        assert _middleware_name("hello") == "str"


# ---------------------------------------------------------------------------
# index controller
# ---------------------------------------------------------------------------


class TestRouteExplorerIndex:
    def _make_request(self, query_params: dict | None = None):
        request = MagicMock()
        params = query_params or {}
        request.query.side_effect = lambda key: params.get(key)
        return request

    def _make_route(self, uri, methods, name="", middleware=None, domain=""):
        route = MagicMock()
        route.uri = uri
        route.methods = methods
        route.name = name
        route._middleware = middleware or []
        route._domain = domain
        return route

    def test_no_router_sets_router_available_false(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request()
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        with (
            patch.object(Admin, "_router", None),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        assert ctx["router_available"] is False
        assert ctx["routes"] == []

    def test_routes_listed(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request()
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        router = MagicMock()
        router.routes.return_value = [
            self._make_route("/users", ["GET", "HEAD"], "users.index"),
            self._make_route("/users/{id}", ["PUT", "PATCH"], "users.update"),
        ]

        with (
            patch.object(Admin, "_router", router),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        assert ctx["router_available"] is True
        assert ctx["total"] == 2

    def test_head_method_excluded(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request()
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        router = MagicMock()
        router.routes.return_value = [
            self._make_route("/api", ["GET", "HEAD"]),
        ]

        with (
            patch.object(Admin, "_router", router),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        route_entry = ctx["routes"][0]
        assert "HEAD" not in route_entry["methods"]
        assert "GET" in route_entry["methods"]

    def test_search_filters_by_uri(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request({"search": "users"})
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        router = MagicMock()
        router.routes.return_value = [
            self._make_route("/users", ["GET"], "users.index"),
            self._make_route("/posts", ["GET"], "posts.index"),
        ]

        with (
            patch.object(Admin, "_router", router),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        assert ctx["total"] == 1
        assert ctx["routes"][0]["uri"] == "/users"

    def test_search_filters_by_name(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request({"search": "posts"})
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        router = MagicMock()
        router.routes.return_value = [
            self._make_route("/articles", ["GET"], "posts.index"),
            self._make_route("/users", ["GET"], "users.index"),
        ]

        with (
            patch.object(Admin, "_router", router),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        assert ctx["total"] == 1
        assert ctx["routes"][0]["name"] == "posts.index"

    def test_method_filter(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request({"method": "POST"})
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        router = MagicMock()
        router.routes.return_value = [
            self._make_route("/login", ["POST"]),
            self._make_route("/logout", ["GET"]),
            self._make_route("/register", ["POST"]),
        ]

        with (
            patch.object(Admin, "_router", router),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        assert ctx["total"] == 2
        assert all("POST" in r["methods"] for r in ctx["routes"])

    def test_middleware_names_resolved(self):
        from hunt.admin.controllers.route_explorer import index

        request = self._make_request()
        base_ctx = MagicMock(return_value={})
        render = MagicMock(return_value=MagicMock())

        class AuthMiddleware:
            pass

        router = MagicMock()
        router.routes.return_value = [
            self._make_route("/secure", ["GET"], middleware=[AuthMiddleware]),
        ]

        with (
            patch.object(Admin, "_router", router),
            patch.object(Admin, "_base_context", base_ctx),
            patch.object(Admin, "_render", render),
        ):
            index(request)

        ctx = render.call_args[0][1]
        assert "AuthMiddleware" in ctx["routes"][0]["middleware"]
