import pytest

from hunt.http.kernel import HttpKernel
from hunt.http.response import JsonResponse, Response
from hunt.http.router import Router
from hunt.testing.test_case import HuntTestCase


def make_kernel() -> HttpKernel:
    router = Router()
    router.get("/", lambda request: Response("Welcome"))
    router.get("/json", lambda request: JsonResponse({"status": "ok"}))
    router.get("/users/{id}", lambda request, id: JsonResponse({"id": id}))
    router.post("/echo", lambda request: JsonResponse(request.all()))
    return HttpKernel(router)


class TestHttpKernel(HuntTestCase):
    def setup_method(self):
        self.kernel = make_kernel()

    @pytest.mark.asyncio
    async def test_get_root(self):
        resp = await self.get("/")
        resp.assert_ok().assert_see("Welcome")

    @pytest.mark.asyncio
    async def test_json_endpoint(self):
        resp = await self.get("/json")
        resp.assert_ok().assert_json("status", "ok")

    @pytest.mark.asyncio
    async def test_path_params(self):
        resp = await self.get("/users/99")
        resp.assert_ok().assert_json("id", "99")

    @pytest.mark.asyncio
    async def test_not_found(self):
        resp = await self.get("/doesnotexist")
        resp.assert_not_found()

    @pytest.mark.asyncio
    async def test_post_echo(self):
        resp = await self.post("/echo", json={"hello": "world"})
        resp.assert_ok().assert_json("hello", "world")
