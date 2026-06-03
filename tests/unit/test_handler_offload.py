from __future__ import annotations

import asyncio
import threading
import time

from hunt.http.kernel import HttpKernel
from hunt.http.response import Response
from hunt.http.router import Router


class _Route:
    """Minimal route stub exposing what _make_handler reads."""

    def __init__(self, action) -> None:
        self.action = action


class _Req:
    pass


def _kernel() -> HttpKernel:
    return HttpKernel(Router())


class TestHandlerOffload:
    def test_sync_handler_runs_inline_by_default(self, monkeypatch):
        monkeypatch.delenv("OFFLOAD_SYNC_HANDLERS", raising=False)
        kernel = _kernel()
        captured: dict = {}

        def action(request):
            captured["ident"] = threading.get_ident()
            return Response("ok")

        handler = kernel._make_handler(_Route(action), {})
        asyncio.run(handler(_Req()))
        assert captured["ident"] == threading.get_ident()  # same (loop) thread

    def test_sync_handler_offloaded_to_thread_when_enabled(self, monkeypatch):
        monkeypatch.setenv("OFFLOAD_SYNC_HANDLERS", "true")
        kernel = _kernel()
        captured: dict = {}

        def action(request):
            captured["ident"] = threading.get_ident()
            return Response("ok")

        handler = kernel._make_handler(_Route(action), {})

        async def go():
            main_ident = threading.get_ident()
            await handler(_Req())
            return main_ident

        main_ident = asyncio.run(go())
        assert captured["ident"] != main_ident  # ran off the loop thread

    def test_async_handler_never_offloaded(self, monkeypatch):
        monkeypatch.setenv("OFFLOAD_SYNC_HANDLERS", "true")
        kernel = _kernel()
        captured: dict = {}

        async def action(request):
            captured["ident"] = threading.get_ident()
            return Response("ok")

        handler = kernel._make_handler(_Route(action), {})

        async def go():
            main_ident = threading.get_ident()
            await handler(_Req())
            return main_ident

        main_ident = asyncio.run(go())
        assert captured["ident"] == main_ident  # async stays on the loop thread

    def test_blocking_handler_does_not_block_loop(self, monkeypatch):
        """Two blocking sync handlers overlap when offloaded, proving the loop is free."""
        monkeypatch.setenv("OFFLOAD_SYNC_HANDLERS", "true")
        kernel = _kernel()

        def slow(request):
            time.sleep(0.2)
            return Response("done")

        handler = kernel._make_handler(_Route(slow), {})

        async def go():
            start = time.monotonic()
            await asyncio.gather(handler(_Req()), handler(_Req()))
            return time.monotonic() - start

        elapsed = asyncio.run(go())
        # Serial would be ~0.4s; concurrent on separate threads is ~0.2s.
        assert elapsed < 0.35
