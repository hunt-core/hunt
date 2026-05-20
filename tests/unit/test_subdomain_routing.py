"""Tests for M18 — subdomain routing."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Route domain matching
# ---------------------------------------------------------------------------


class TestRouteDomainMatching:
    def _make_route(self, uri, domain=None):
        from hunt.http.route import Route

        return Route(["GET"], uri, lambda req: None, domain=domain)

    def test_no_domain_matches_any_host(self):
        route = self._make_route("/users")
        matched, _ = route.matches("GET", "/users", host="anything.example.com")
        assert matched

    def test_no_domain_matches_no_host(self):
        route = self._make_route("/users")
        matched, _ = route.matches("GET", "/users", host=None)
        assert matched

    def test_static_domain_matches_exact_host(self):
        route = self._make_route("/users", domain="api.example.com")
        matched, _ = route.matches("GET", "/users", host="api.example.com")
        assert matched

    def test_static_domain_rejects_wrong_host(self):
        route = self._make_route("/users", domain="api.example.com")
        matched, _ = route.matches("GET", "/users", host="www.example.com")
        assert not matched

    def test_static_domain_rejects_no_host(self):
        route = self._make_route("/users", domain="api.example.com")
        matched, _ = route.matches("GET", "/users", host=None)
        assert not matched

    def test_param_domain_captures_segment(self):
        route = self._make_route("/dashboard", domain="{account}.example.com")
        matched, params = route.matches("GET", "/dashboard", host="acme.example.com")
        assert matched
        assert params["account"] == "acme"

    def test_param_domain_rejects_non_matching_host(self):
        route = self._make_route("/dashboard", domain="{account}.example.com")
        matched, _ = route.matches("GET", "/dashboard", host="example.com")
        assert not matched

    def test_domain_and_path_params_merged(self):
        route = self._make_route("/users/{id}", domain="{account}.example.com")
        matched, params = route.matches("GET", "/users/42", host="acme.example.com")
        assert matched
        assert params["account"] == "acme"
        assert params["id"] == "42"

    def test_domain_case_insensitive(self):
        route = self._make_route("/", domain="API.example.com")
        matched, _ = route.matches("GET", "/", host="api.example.com")
        assert matched

    def test_static_subdomain_only(self):
        """Router.domain('api') matches host 'api' exactly (e.g. localhost testing)."""
        route = self._make_route("/", domain="api")
        matched, _ = route.matches("GET", "/", host="api")
        assert matched

    def test_param_subdomain_only(self):
        route = self._make_route("/", domain="{tenant}")
        matched, params = route.matches("GET", "/", host="acme")
        assert matched
        assert params["tenant"] == "acme"


# ---------------------------------------------------------------------------
# Router.domain() context manager / group helper
# ---------------------------------------------------------------------------


class TestRouterDomainGroup:
    def _router(self):
        from hunt.http.router import Router

        return Router()

    def test_domain_group_sets_domain_on_routes(self):
        router = self._router()

        def register(r):
            r.get("/hello", lambda req: None)

        router.domain("api.example.com").group(register)
        assert len(router.routes()) == 1
        assert router.routes()[0]._domain == "api.example.com"

    def test_routes_outside_domain_group_have_no_domain(self):
        router = self._router()
        router.get("/open", lambda req: None)
        assert router.routes()[0]._domain is None

    def test_domain_group_combined_with_prefix(self):
        router = self._router()

        def register(r):
            with r.group(prefix="/v1"):
                r.get("/status", lambda req: None)

        router.domain("api.example.com").group(register)
        route = router.routes()[0]
        assert route.uri == "/v1/status"
        assert route._domain == "api.example.com"

    def test_domain_group_dispatch_matches_correct_host(self):
        router = self._router()

        def register(r):
            r.get("/data", lambda req: None)

        router.domain("api.example.com").group(register)
        route, params = router.dispatch("GET", "/data", host="api.example.com")
        assert route is not None

    def test_domain_group_dispatch_rejects_wrong_host(self):
        from hunt.http.router import RouteNotFoundException

        router = self._router()

        def register(r):
            r.get("/data", lambda req: None)

        router.domain("api.example.com").group(register)
        with pytest.raises(RouteNotFoundException):
            router.dispatch("GET", "/data", host="www.example.com")

    def test_dispatch_without_host_skips_domain_routes(self):
        from hunt.http.router import RouteNotFoundException

        router = self._router()
        router.domain("api.example.com").group(lambda r: r.get("/data", lambda req: None))
        with pytest.raises(RouteNotFoundException):
            router.dispatch("GET", "/data", host=None)

    def test_param_domain_captured_in_dispatch(self):
        router = self._router()
        router.domain("{account}.example.com").group(lambda r: r.get("/dash", lambda req: None))
        _, params = router.dispatch("GET", "/dash", host="acme.example.com")
        assert params["account"] == "acme"


# ---------------------------------------------------------------------------
# Request.host and Request.subdomain
# ---------------------------------------------------------------------------


class TestRequestHost:
    def _request(self, host_header: str) -> object:
        from hunt.http.request import Request

        return Request(
            scope={
                "method": "GET",
                "path": "/",
                "headers": [(b"host", host_header.encode())],
            }
        )

    def test_host_strips_port(self):
        req = self._request("api.example.com:8080")
        assert req.host == "api.example.com"

    def test_host_no_port(self):
        req = self._request("api.example.com")
        assert req.host == "api.example.com"

    def test_host_lowercases(self):
        req = self._request("API.Example.COM")
        assert req.host == "api.example.com"

    def test_subdomain_strips_root(self):
        req = self._request("api.example.com")
        assert req.subdomain("example.com") == "api"

    def test_subdomain_when_host_equals_root(self):
        req = self._request("example.com")
        assert req.subdomain("example.com") == ""

    def test_subdomain_when_not_a_suffix(self):
        req = self._request("other.com")
        assert req.subdomain("example.com") == ""

    def test_subdomain_multi_level(self):
        req = self._request("a.b.example.com")
        assert req.subdomain("example.com") == "a.b"
