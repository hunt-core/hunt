from __future__ import annotations

import os
from typing import Any

_client: Any = None


def get_redis() -> Any:
    """Return the shared Redis client, configured via REDIS_* env vars.

    Strings are decoded automatically (decode_responses=True).
    """
    global _client
    if _client is None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis-py is required. Install it: pip install redis") from exc
        kwargs: dict[str, Any] = {
            "host": os.environ.get("REDIS_HOST", "127.0.0.1"),
            "port": int(os.environ.get("REDIS_PORT", "6379")),
            "db": int(os.environ.get("REDIS_DB", "0")),
            "decode_responses": True,
        }
        password = os.environ.get("REDIS_PASSWORD")
        if password:
            kwargs["password"] = password
        _client = redis.Redis(**kwargs)
    return _client
