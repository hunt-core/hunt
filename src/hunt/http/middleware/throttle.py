from __future__ import annotations

import asyncio
import time
import uuid

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, Response

# In-process state — NOT shared across multiple workers.
_hits: dict[str, list[float]] = {}
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


class ThrottleRequests(Middleware):
    """Single-process sliding-window rate limiter.

    Limits are NOT shared across Uvicorn workers. For multi-worker production
    deployments use RedisThrottleRequests instead.
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


# ---------------------------------------------------------------------------
# Async Redis sliding-window rate limiter
# ---------------------------------------------------------------------------

_redis_client: object | None = None
_redis_lock: asyncio.Lock | None = None


def _get_redis_lock() -> asyncio.Lock:
    global _redis_lock
    if _redis_lock is None:
        _redis_lock = asyncio.Lock()
    return _redis_lock


async def _get_async_redis() -> object:
    """Return a shared async Redis client, initialised once per process."""
    global _redis_client
    if _redis_client is None:
        async with _get_redis_lock():
            if _redis_client is None:
                import os

                try:
                    from redis.asyncio import Redis as AsyncRedis
                except ImportError as exc:
                    raise RuntimeError(
                        "redis-py is required for RedisThrottleRequests. Install it: pip install redis"
                    ) from exc

                url = os.environ.get("REDIS_URL", "")
                if url:
                    _redis_client = AsyncRedis.from_url(url, decode_responses=True)
                else:
                    _redis_client = AsyncRedis(
                        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                        port=int(os.environ.get("REDIS_PORT", "6379")),
                        db=int(os.environ.get("REDIS_DB", "0")),
                        password=os.environ.get("REDIS_PASSWORD") or None,
                        decode_responses=True,
                    )
    return _redis_client


class RedisThrottleRequests(Middleware):
    """Async Redis sliding-window rate limiter — safe for multi-worker deployments.

    Requires the ``redis`` package (``pip install redis``).
    Configure the connection via REDIS_URL or REDIS_HOST/PORT/DB/PASSWORD env vars.

    Subclass and override ``max_attempts`` / ``decay_seconds`` to tune limits:

        class ThrottleLogin(RedisThrottleRequests):
            max_attempts = 5
            decay_seconds = 60
    """

    max_attempts: int = 60
    decay_seconds: int = 60

    async def handle(self, request: Request, next: Next) -> Response:
        key = f"throttle:{self._resolve_key(request)}"
        now = time.time()
        window_start = now - self.decay_seconds
        member = f"{now}:{uuid.uuid4().hex}"

        redis = await _get_async_redis()
        pipe = redis.pipeline()  # type: ignore[attr-defined]
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {member: now})
        pipe.expire(key, self.decay_seconds + 1)
        results = await pipe.execute()

        count_before = int(results[1])
        if count_before >= self.max_attempts:
            # Undo the ZADD we just performed
            await redis.zrem(key, member)  # type: ignore[attr-defined]
            oldest = await redis.zrange(key, 0, 0, withscores=True)  # type: ignore[attr-defined]
            retry_after = int(self.decay_seconds - (now - oldest[0][1])) if oldest else self.decay_seconds
            raise HttpException(429, f"Too many requests. Retry after {retry_after}s.")

        remaining = max(0, self.max_attempts - count_before - 1)
        response = await next(request)
        response.header("X-RateLimit-Limit", str(self.max_attempts))
        response.header("X-RateLimit-Remaining", str(remaining))
        return response

    def _resolve_key(self, request: Request) -> str:
        return f"{request.ip}:{request.path}"
