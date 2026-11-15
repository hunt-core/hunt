from __future__ import annotations

import asyncio
import ipaddress
import math
import re
import time
import uuid

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import HttpException, Response

# In-process state — NOT shared across multiple workers.
_hits: dict[str, list[float]] = {}
# When each key's window ends (last hit + that limiter's decay). Lets the sweep
# drop idle keys without knowing which limiter wrote them.
_expires: dict[str, float] = {}
_lock: asyncio.Lock | None = None
_last_sweep: float = 0.0

_SWEEP_INTERVAL = 30.0  # seconds between full sweeps
_MAX_KEYS = 100_000  # hard cap on tracked keys
_FALLBACK_TTL = 3600.0  # for keys with no recorded expiry


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _sweep(now: float, force: bool = False) -> None:
    """Drop every key whose window has passed; enforce the key cap.

    Without this each distinct client/route pair would stay in ``_hits`` forever.
    """
    global _last_sweep
    if not force and now - _last_sweep < _SWEEP_INTERVAL and len(_hits) <= _MAX_KEYS:
        return
    _last_sweep = now
    for key in list(_hits):
        stamps = _hits[key]
        expiry = _expires.get(key)
        if expiry is None:
            expiry = (stamps[-1] if stamps else 0.0) + _FALLBACK_TTL
        if not stamps or expiry <= now:
            _hits.pop(key, None)
            _expires.pop(key, None)
    for key in [k for k in _expires if k not in _hits]:
        del _expires[key]
    if len(_hits) > _MAX_KEYS:
        # Still over the cap (a flood of distinct keys): evict the stalest.
        overflow = len(_hits) - _MAX_KEYS
        for key in sorted(_hits, key=lambda k: _hits[k][-1] if _hits[k] else 0.0)[:overflow]:
            _hits.pop(key, None)
            _expires.pop(key, None)


def _client_key(ip: str) -> str:
    """The client part of a throttle key. IPv6 is masked to its /64: one
    subscriber controls a whole /64 and could otherwise rotate addresses."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return str(ip)
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address) + "/64"
    return str(addr)


def _route_key(request: Request) -> str:
    """The route part of a throttle key.

    The matched route's URI *pattern* is used, so ``/login``, ``/login/`` and
    ``/login//`` share a bucket, as do ``/users/1`` and ``/users/2``. When no
    route matched, the path is normalised instead.
    """
    route = getattr(request, "route", None)
    uri = getattr(route, "uri", None) if route is not None else None
    if isinstance(uri, str) and uri:
        return uri
    path = str(getattr(request, "path", "/") or "/")
    return re.sub(r"/{2,}", "/", path).rstrip("/") or "/"


def _throttle_key(request: Request) -> str:
    # Uses the transport-layer IP only — not X-Forwarded-For which is spoofable
    return f"{_client_key(str(request.ip))}:{_route_key(request)}"


async def _load_body(request: Request) -> None:
    loader = getattr(request, "load_body", None)
    if loader is not None:
        await loader()


def _too_many(max_attempts: int, retry_after: int) -> HttpException:
    retry_after = max(1, int(retry_after))
    return HttpException(
        429,
        f"Too many requests. Retry after {retry_after}s.",
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(max_attempts),
            "X-RateLimit-Remaining": "0",
        },
    )


class ThrottleRequests(Middleware):
    """Single-process sliding-window rate limiter.

    Limits are NOT shared across Uvicorn workers. For multi-worker production
    deployments use RedisThrottleRequests instead.
    """

    reads_body = False  # never touches the request body

    max_attempts: int = 60
    decay_seconds: int = 60

    async def handle(self, request: Request, next: Next) -> Response:
        if type(self)._resolve_key is not ThrottleRequests._resolve_key:
            # A custom key may be built from request input (e.g. the login e-mail).
            await _load_body(request)
        key = self._resolve_key(request)
        now = time.time()
        window_start = now - self.decay_seconds

        async with _get_lock():
            _sweep(now)
            timestamps = [t for t in _hits.get(key, []) if t > window_start]
            if len(timestamps) >= self.max_attempts:
                _hits[key] = timestamps
                _expires[key] = timestamps[-1] + self.decay_seconds
                raise _too_many(self.max_attempts, math.ceil(self.decay_seconds - (now - timestamps[0])))
            timestamps.append(now)
            _hits[key] = timestamps
            _expires[key] = now + self.decay_seconds

        response = await next(request)
        response.header("X-RateLimit-Limit", str(self.max_attempts))
        response.header("X-RateLimit-Remaining", str(max(0, self.max_attempts - len(timestamps))))
        return response

    def _resolve_key(self, request: Request) -> str:
        return _throttle_key(request)


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
                try:
                    from redis.asyncio import Redis as AsyncRedis
                except ImportError as exc:
                    raise RuntimeError(
                        "redis-py is required for RedisThrottleRequests. Install it: pip install redis"
                    ) from exc

                from hunt.redis_connection import redis_options

                # Same settings (REDIS_URL, TLS, credentials, timeouts) as the
                # shared synchronous client.
                url, kwargs = redis_options()
                if url:
                    _redis_client = AsyncRedis.from_url(url, **kwargs)
                else:
                    _redis_client = AsyncRedis(**kwargs)
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

    reads_body = False  # never touches the request body

    max_attempts: int = 60
    decay_seconds: int = 60

    async def handle(self, request: Request, next: Next) -> Response:
        if type(self)._resolve_key is not RedisThrottleRequests._resolve_key:
            # A custom key may be built from request input (e.g. the login e-mail).
            await _load_body(request)
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
            retry_after = math.ceil(self.decay_seconds - (now - oldest[0][1])) if oldest else self.decay_seconds
            raise _too_many(self.max_attempts, retry_after)

        remaining = max(0, self.max_attempts - count_before - 1)
        response = await next(request)
        response.header("X-RateLimit-Limit", str(self.max_attempts))
        response.header("X-RateLimit-Remaining", str(remaining))
        return response

    def _resolve_key(self, request: Request) -> str:
        return _throttle_key(request)
