from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from hunt.session.store import storage_key

_log = logging.getLogger("hunt.session")

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{64}$")

# Sessions live at HASHED_KEY_PREFIX + sha256(session_id). Releases before
# hashed storage used LEGACY_KEY_PREFIX + session_id; the namespaces differ so
# that a digest can never be mistaken for (and used as) a raw session ID.
HASHED_KEY_PREFIX = "hunt:session:sha256:"
LEGACY_KEY_PREFIX = "hunt:session:"


def hashed_key(key_hash: str) -> str:
    """Redis key for the session whose ``sha256(session_id)`` is *key_hash*."""
    return f"{HASHED_KEY_PREFIX}{key_hash}"


def legacy_key(session_id: str) -> str:
    """Redis key used for *session_id* before sessions were stored hashed."""
    return f"{LEGACY_KEY_PREFIX}{session_id}"


def is_missing_key_error(exc: Exception) -> bool:
    """True for the error RENAME raises when the source key does not exist."""
    return "no such key" in str(exc).lower()


def _session_lifetime() -> int:
    try:
        from hunt.support.helpers import config as _cfg

        return int(_cfg("session.lifetime", 7200))
    except Exception:
        return 7200


class RedisSessionStore:
    """Redis-backed session store. Each session is a Redis string with TTL.

    The key is derived from ``sha256(session_id)``, never the session ID. A
    session stored by an earlier release under its raw ID is still read, and
    the next save() moves it to the hashed key.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._id: str | None = None
        self._started_id: str | None = None
        self._existed = False
        self._read_failed = False
        self._legacy = False

    def _redis(self) -> Any:
        from hunt.redis_connection import get_redis

        return get_redis()

    def _key(self, session_id: str) -> str:
        return hashed_key(storage_key(session_id))

    def _legacy_key(self, session_id: str) -> str:
        return legacy_key(session_id)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, session_id: str) -> None:
        """Load the session for *session_id*, or begin a fresh one.

        An ID is adopted only when Redis holds a record for it; a malformed or
        unknown ID is replaced by a newly generated one. If Redis cannot be
        *read* the client's ID is kept and ``read_failed`` is set: the request
        runs with an empty session, and save() will not overwrite the real one.
        """
        self._read_failed = False
        self._legacy = False
        data: dict[str, Any] | None = None
        if _SESSION_ID_RE.match(session_id or ""):
            try:
                data = self._read(session_id)
                if data is None:
                    data = self._read_legacy(session_id)
                    self._legacy = data is not None
            except Exception as exc:
                _log.error("Session read failed; this request runs without its session: %s", exc)
                self._read_failed = True
                data = {}
        if data is None:
            session_id = os.urandom(32).hex()
            data = {}
            self._existed = False
        else:
            self._existed = not self._read_failed
        self._id = session_id
        self._started_id = session_id
        self._data = data

    @property
    def existed(self) -> bool:
        """True when start() found a record (i.e. this is not a new session)."""
        return self._existed

    @property
    def started_id(self) -> str | None:
        """The session ID as of start(); differs from ``id`` after regenerate()."""
        return self._started_id

    @property
    def read_failed(self) -> bool:
        """True when start() could not reach Redis; save() is then a no-op."""
        return self._read_failed

    def age_flash(self) -> None:
        self._data["_flash_old"] = self._data.pop("_flash_new", {})

    def save(self) -> bool:
        """Persist the session. Returns False when nothing was written.

        Not written when the session could not be read at start() (the empty
        dict would replace the real data), nor when a session that existed at
        start() is gone from Redis by now (``SET ... XX``): it was revoked or
        logged out meanwhile and must not come back.
        """
        if not self._id:
            return False
        if self._read_failed:
            _log.warning("Session not saved: it could not be read at the start of the request.")
            return False
        lifetime = _session_lifetime()
        payload = json.dumps(self._data)
        try:
            if self._existed and self._id == self._started_id:
                client = self._redis()
                key = self._key(self._id)
                if self._legacy:
                    # Move the legacy record to the hashed key. RENAME is atomic
                    # and fails when the source is gone (revoked, or already
                    # moved by a concurrent request); SET XX then decides.
                    try:
                        client.rename(self._legacy_key(self._id), key)
                    except Exception as exc:
                        if not is_missing_key_error(exc):
                            raise
                if not client.set(key, payload, ex=lifetime, xx=True):
                    return False
                self._legacy = False
                return True
            self._redis().setex(self._key(self._id), lifetime, payload)
            return True
        except Exception as exc:
            _log.error("Session save failed: %s", exc)
            return False

    def regenerate(self) -> str:
        if self._id:
            self._redis().delete(self._key(self._id))
            if self._legacy:
                self._redis().delete(self._legacy_key(self._id))
        self._legacy = False
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

    def _read(self, session_id: str) -> dict[str, Any] | None:
        """Return the stored data, or None when Redis has no record.

        Connection and timeout errors propagate: "missing" and "unreachable"
        must not look the same to start().
        """
        return self._decode(self._redis().get(self._key(session_id)))

    def _read_legacy(self, session_id: str) -> dict[str, Any] | None:
        """Return the data stored under the pre-hash key, or None."""
        return self._decode(self._redis().get(self._legacy_key(session_id)))

    @staticmethod
    def _decode(raw: Any) -> dict[str, Any] | None:
        if raw is None or raw == "" or raw == b"":
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
