from __future__ import annotations

import os
from typing import Any

_client: Any = None

_DEFAULT_SOCKET_TIMEOUT = 5.0
_DEFAULT_HEALTH_CHECK_INTERVAL = 30


def _float_env(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return max(minimum, value)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def redis_options() -> tuple[str, dict[str, Any]]:
    """Return ``(url, kwargs)`` for building a Redis client from the environment.

    ``REDIS_URL`` (``redis://``, ``rediss://`` for TLS, ``unix://``) wins when
    set; then ``url`` is non-empty and ``kwargs`` holds only the options that
    apply on top of it. Otherwise ``url`` is ``""`` and ``kwargs`` carries the
    REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_USERNAME / REDIS_PASSWORD /
    REDIS_SSL settings.

    Socket timeouts are always set (REDIS_SOCKET_TIMEOUT and
    REDIS_SOCKET_CONNECT_TIMEOUT, default and minimum 5 seconds) so a stalled
    Redis cannot hang a worker forever, and connections are health-checked
    every REDIS_HEALTH_CHECK_INTERVAL seconds (default 30).
    """
    kwargs: dict[str, Any] = {
        "decode_responses": True,
        "socket_timeout": _float_env("REDIS_SOCKET_TIMEOUT", _DEFAULT_SOCKET_TIMEOUT, _DEFAULT_SOCKET_TIMEOUT),
        "socket_connect_timeout": _float_env(
            "REDIS_SOCKET_CONNECT_TIMEOUT", _DEFAULT_SOCKET_TIMEOUT, _DEFAULT_SOCKET_TIMEOUT
        ),
        "health_check_interval": max(0, _int_env("REDIS_HEALTH_CHECK_INTERVAL", _DEFAULT_HEALTH_CHECK_INTERVAL)),
    }
    url = os.environ.get("REDIS_URL", "").strip()
    if url:
        return url, kwargs

    kwargs["host"] = os.environ.get("REDIS_HOST", "127.0.0.1")
    kwargs["port"] = _int_env("REDIS_PORT", 6379)
    kwargs["db"] = _int_env("REDIS_DB", 0)
    username = os.environ.get("REDIS_USERNAME")
    if username:
        kwargs["username"] = username
    password = os.environ.get("REDIS_PASSWORD")
    if password:
        kwargs["password"] = password
    if os.environ.get("REDIS_SSL", "").strip().lower() in ("true", "1", "yes"):
        kwargs["ssl"] = True
    return "", kwargs


def get_redis() -> Any:
    """Return the shared Redis client, configured from the environment.

    Honours ``REDIS_URL`` first, then the individual ``REDIS_*`` variables (see
    :func:`redis_options`). Strings are decoded automatically
    (decode_responses=True) and socket timeouts are always set.
    """
    global _client
    if _client is None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis-py is required. Install it: pip install redis") from exc
        url, kwargs = redis_options()
        _client = redis.Redis.from_url(url, **kwargs) if url else redis.Redis(**kwargs)
    return _client


def reset_redis() -> None:
    """Forget the shared client (e.g. after the environment changed, or in tests)."""
    global _client
    _client = None
