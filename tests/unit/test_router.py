import pytest

from hunt.http.router import RouteNotFoundException, Router


def test_basic_route_match():
    r = Router()
    r.get("/hello", lambda req: "world")
    _route, params = r.dispatch("GET", "/hello")
    assert params == {}


def test_path_params():
    r = Router()
    r.get("/users/{id}", lambda req, id: id)
    _route, params = r.dispatch("GET", "/users/42")
    assert params == {"id": "42"}


def test_named_route_url():
    r = Router()
    r.get("/posts/{slug}", lambda req, slug: slug).named("posts.show")
    r._register_named(r.routes()[0])
    url = r.url("posts.show", {"slug": "my-post"})
    assert url == "/posts/my-post"


def test_not_found_raises():
    r = Router()
    with pytest.raises(RouteNotFoundException):
        r.dispatch("GET", "/nowhere")


def test_group_prefix():
    r = Router()
    with r.group(prefix="/api"):
        r.get("/users", lambda req: [])
    route, _ = r.dispatch("GET", "/api/users")
    assert route.uri == "/api/users"
