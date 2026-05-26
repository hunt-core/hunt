"""Tests for hunt.http.middleware.throttle.ThrottleRequests."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from hunt.http.middleware.throttle import ThrottleRequests, _hits
from hunt.http.response import HttpException


def _make_request(ip="127.0.0.1", path="/test"):
    request = MagicMock()
    request.ip = ip
    request.path = path
    return request


def _make_next(response=None):
    resp = response or MagicMock()
    resp.header = MagicMock()
    return AsyncMock(return_value=resp)


# ---------------------------------------------------------------------------
# ThrottleRequests
# ---------------------------------------------------------------------------


class TestThrottleRequests:
    def setup_method(self):
        _hits.clear()

    def test_allows_request_below_limit(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 5
        middleware.decay_seconds = 60
        request = _make_request()
        next_fn = _make_next()

        asyncio.run(middleware.handle(request, next_fn))
        next_fn.assert_awaited_once()

    def test_allows_exactly_at_limit(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 3
        middleware.decay_seconds = 60
        request = _make_request()

        key = f"{request.ip}:{request.path}"
        _hits[key] = [time.time() - 1] * 2  # 2 previous hits

        next_fn = _make_next()
        asyncio.run(middleware.handle(request, next_fn))
        next_fn.assert_awaited_once()

    def test_raises_429_when_over_limit(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 3
        middleware.decay_seconds = 60
        request = _make_request()

        key = f"{request.ip}:{request.path}"
        now = time.time()
        _hits[key] = [now - 2, now - 1, now - 0.5]  # already at limit

        next_fn = _make_next()
        with pytest.raises(HttpException) as exc_info:
            asyncio.run(middleware.handle(request, next_fn))

        assert exc_info.value.status == 429
        next_fn.assert_not_awaited()

    def test_expired_hits_not_counted(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 2
        middleware.decay_seconds = 10
        request = _make_request()

        key = f"{request.ip}:{request.path}"
        # All hits older than the window
        _hits[key] = [time.time() - 20, time.time() - 15]

        next_fn = _make_next()
        asyncio.run(middleware.handle(request, next_fn))
        next_fn.assert_awaited_once()

    def test_rate_limit_headers_set(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 5
        middleware.decay_seconds = 60
        request = _make_request()
        resp = MagicMock()
        resp.header = MagicMock()
        next_fn = AsyncMock(return_value=resp)

        asyncio.run(middleware.handle(request, next_fn))

        header_calls = {call.args[0]: call.args[1] for call in resp.header.call_args_list}
        assert "X-RateLimit-Limit" in header_calls
        assert header_calls["X-RateLimit-Limit"] == "5"
        assert "X-RateLimit-Remaining" in header_calls

    def test_remaining_decrements_on_each_request(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 5
        middleware.decay_seconds = 60

        results = []
        for _ in range(3):
            request = _make_request()
            resp = MagicMock()
            resp.header = MagicMock()
            next_fn = AsyncMock(return_value=resp)
            asyncio.run(middleware.handle(request, next_fn))
            calls = {c.args[0]: c.args[1] for c in resp.header.call_args_list}
            results.append(int(calls["X-RateLimit-Remaining"]))

        assert results[0] > results[1] > results[2]

    def test_different_ips_have_separate_limits(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 1
        middleware.decay_seconds = 60

        req1 = _make_request(ip="1.2.3.4")
        req2 = _make_request(ip="5.6.7.8")

        key1 = f"{req1.ip}:{req1.path}"
        _hits[key1] = [time.time() - 0.5]  # ip1 is at limit

        # ip2 should still be allowed
        next_fn = _make_next()
        asyncio.run(middleware.handle(req2, next_fn))
        next_fn.assert_awaited_once()

    def test_different_paths_have_separate_limits(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 1
        middleware.decay_seconds = 60

        req1 = _make_request(path="/api/login")
        req2 = _make_request(path="/api/register")

        key1 = f"{req1.ip}:{req1.path}"
        _hits[key1] = [time.time() - 0.5]  # /api/login is at limit

        # /api/register should still be allowed
        next_fn = _make_next()
        asyncio.run(middleware.handle(req2, next_fn))
        next_fn.assert_awaited_once()

    def test_resolve_key_uses_ip_and_path(self):
        middleware = ThrottleRequests()
        request = _make_request(ip="10.0.0.1", path="/api/test")
        key = middleware._resolve_key(request)
        assert key == "10.0.0.1:/api/test"

    def test_retry_after_in_429_message(self):
        middleware = ThrottleRequests()
        middleware.max_attempts = 1
        middleware.decay_seconds = 30
        request = _make_request()

        key = middleware._resolve_key(request)
        _hits[key] = [time.time() - 5]  # 5 seconds old, 25 seconds remaining

        with pytest.raises(HttpException) as exc_info:
            asyncio.run(middleware.handle(request, _make_next()))

        assert "Retry after" in str(exc_info.value.message)

    def test_default_max_attempts_is_60(self):
        assert ThrottleRequests.max_attempts == 60

    def test_default_decay_seconds_is_60(self):
        assert ThrottleRequests.decay_seconds == 60

    def test_subclass_can_override_limits(self):
        class StrictThrottle(ThrottleRequests):
            max_attempts = 3
            decay_seconds = 300

        assert StrictThrottle.max_attempts == 3
        assert StrictThrottle.decay_seconds == 300
