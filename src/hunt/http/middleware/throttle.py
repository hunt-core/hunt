from __future__ import annotations

import asyncio
import time

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, Response

# NOTE: This rate limiter is single-process only. In a multi-worker deployment
# (multiple uvicorn workers or gunicorn) each worker has its own counter and
# limits are NOT shared. For production multi-worker setups, replace _hits with
# a Redis or database-backed store.
_hits: dict[str, list[float]] = {}
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


class ThrottleRequests(Middleware):
    """Single-process sliding-window rate limiter.

    For multi-worker deployments, this counter is NOT shared across workers.
    Configure a Redis-backed store for production rate limiting.
    """

    max_attempts: int = 60
    decay_seconds: int = 60

    async def handle(self, request: Request, next: Next) -> Response:
        key = self._resolve_key(request)
        now = time.time()
        window_start = now - self.decay_seconds

        async with _get_lock():
            timestamps = [t for t in _hits.get(key, []) if t > window_start]
            if len(timestamps) >= self.max_attempts:
                retry_after = int(self.decay_seconds - (now - timestamps[0]))
                raise HttpException(429, f"Too many requests. Retry after {retry_after}s.")
            timestamps.append(now)
            _hits[key] = timestamps

        response = await next(request)
        response.header("X-RateLimit-Limit", str(self.max_attempts))
        response.header("X-RateLimit-Remaining", str(max(0, self.max_attempts - len(timestamps))))
        return response

    def _resolve_key(self, request: Request) -> str:
        # Uses the transport-layer IP only — not X-Forwarded-For which is spoofable
        return f"{request.ip}:{request.path}"
