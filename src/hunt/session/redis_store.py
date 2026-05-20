from __future__ import annotations

import json
import os
import re
from typing import Any

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def _session_lifetime() -> int:
    try:
        from hunt.support.helpers import config as _cfg

        return int(_cfg("session.lifetime", 7200))
    except Exception:
        return 7200


class RedisSessionStore:
    """Redis-backed session store. Each session is a Redis string with TTL."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._id: str | None = None

    def _redis(self) -> Any:
        from hunt.redis_connection import get_redis

        return get_redis()

    def _key(self, session_id: str) -> str:
        return f"hunt:session:{session_id}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, session_id: str) -> None:
        if not _SESSION_ID_RE.match(session_id):
            session_id = os.urandom(32).hex()
        self._id = session_id
        self._data = self._read(session_id)

    def age_flash(self) -> None:
        self._data["_flash_old"] = self._data.pop("_flash_new", {})

    def save(self) -> None:
        if not self._id:
            return
        lifetime = _session_lifetime()
        self._redis().setex(self._key(self._id), lifetime, json.dumps(self._data))

    def regenerate(self) -> str:
        if self._id:
            self._redis().delete(self._key(self._id))
        self._id = os.urandom(32).hex()
        return self._id

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def put(self, key: str, value: Any) -> None:
        self._data[key] = value

    def forget(self, key: str) -> None:
        self._data.pop(key, None)

    def pull(self, key: str, default: Any = None) -> Any:
        return self._data.pop(key, default)

    def remember(self, key: str, callback: Any) -> Any:
        if key in self._data:
            return self._data[key]
        value = callback() if callable(callback) else callback
        self._data[key] = value
        return value

    def flush(self) -> None:
        self._data = {}

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    def has(self, key: str) -> bool:
        return key in self._data

    # ------------------------------------------------------------------
    # Flash
    # ------------------------------------------------------------------

    def flash(self, key: str, value: Any) -> None:
        self._data.setdefault("_flash_new", {})[key] = value

    def get_flash(self, key: str, default: Any = None) -> Any:
        return self._data.get("_flash_old", {}).get(key, default)

    def has_flash(self, key: str) -> bool:
        return key in self._data.get("_flash_old", {})

    def all_flash(self) -> dict[str, Any]:
        return self._data.get("_flash_old", {})

    # ------------------------------------------------------------------
    # CSRF token
    # ------------------------------------------------------------------

    def csrf_token(self) -> str:
        token = self._data.get("_csrf_token")
        if not token:
            token = os.urandom(32).hex()
            self._data["_csrf_token"] = token
        return token

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> str | None:
        return self._id

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _read(self, session_id: str) -> dict[str, Any]:
        raw = self._redis().get(self._key(session_id))
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}
