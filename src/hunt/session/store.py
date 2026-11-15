from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{64}$")

# Marks a session file written under the hashed name. A file without it is a
# legacy record named after the raw session ID (see FileSessionStore).
HASHED_FLAG = "hashed"


def storage_key(session_id: str) -> str:
    """Return the key a session is stored under: ``sha256(session_id)`` hex.

    The session ID is a bearer credential (it is the cookie value). Only this
    digest is ever written to disk, Redis or the ``user_sessions`` table, so
    reading the session storage does not yield a usable cookie.
    """
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()


def load_payload(path: Path) -> dict[str, Any] | None:
    """Return the JSON object stored in a session file, or None (missing, unreadable, not an object)."""
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def is_hashed_payload(payload: dict[str, Any]) -> bool:
    return payload.get(HASHED_FLAG) is True


def write_payload(directory: Path, path: Path, payload: dict[str, Any], *, exclusive: bool = False) -> bool:
    """Atomically write *payload* to *path* with mode 0600.

    With ``exclusive`` the file is only created when *path* does not exist
    yet; returns False when it already does.
    """
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".sess_")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(payload))
        os.chmod(tmp_path, 0o600)
        if exclusive:
            try:
                os.link(tmp_path, path)
            except FileExistsError:
                os.unlink(tmp_path)
                return False
            except OSError:
                # No hard links on this filesystem: check, then replace.
                if os.path.exists(path):
                    os.unlink(tmp_path)
                    return False
                os.replace(tmp_path, path)
                return True
            os.unlink(tmp_path)
            return True
        os.replace(tmp_path, path)
        return True
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _session_lifetime() -> int:
    try:
        from hunt.support.helpers import config as _cfg

        return int(_cfg("session.lifetime", 7200))
    except Exception:
        return 7200


class FileSessionStore:
    """File-backed session store. One JSON file per session.

    The file is named ``sha256(session_id)``, never the session ID itself, and
    its payload carries ``"hashed": true``.

    Sessions written by earlier releases are files named after the raw ID,
    without that flag. They are still read, and the next save() writes the
    session under the hashed name and removes the legacy file. A flagged file
    is never accepted as a legacy record: otherwise the digest of a session
    (which is what an attacker reading the directory sees) would itself work
    as a cookie.
    """

    LIFETIME = 7200  # 2 hours — override via config/session.py session.lifetime

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        self._id: str | None = None
        self._started_id: str | None = None
        self._existed = False
        self._legacy = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, session_id: str) -> None:
        """Load the session for *session_id*, or begin a fresh one.

        An ID is adopted only when the store already holds a live record for
        it. A malformed, unknown, expired or revoked ID is replaced by a newly
        generated one, so a client (or an attacker planting a cookie) can never
        choose the ID of a session.
        """
        data: dict[str, Any] | None = None
        self._legacy = False
        if _SESSION_ID_RE.match(session_id or ""):
            data = self._read(session_id)
            if data is None:
                data = self._read_legacy(session_id)
                self._legacy = data is not None
        if data is None:
            session_id = os.urandom(32).hex()
            data = {}
            self._existed = False
        else:
            self._existed = True
        self._id = session_id
        self._started_id = session_id
        self._data = data
        if random.random() < 0.01:
            import asyncio

            try:
                asyncio.get_running_loop().run_in_executor(None, self._gc)
            except RuntimeError:
                self._gc()

    def age_flash(self) -> None:
        """Promote new flash data to old so it's readable this request."""
        self._data["_flash_old"] = self._data.pop("_flash_new", {})

    @property
    def existed(self) -> bool:
        """True when start() found a live record (i.e. this is not a new session)."""
        return self._existed

    @property
    def started_id(self) -> str | None:
        """The session ID as of start(); differs from ``id`` after regenerate()."""
        return self._started_id

    def save(self) -> bool:
        """Persist the session. Returns False when nothing was written.

        A session that existed at start() and whose file has since been deleted
        (revoked, or logged out by a concurrent request) is *not* written back:
        doing so would resurrect it, still authenticated.
        """
        if not self._id:
            return False
        path = self._file(self._id)
        if self._existed and self._id == self._started_id:
            alive = path.exists() or (self._legacy and self._legacy_file(self._id).exists())
            if not alive:
                return False
        lifetime = _session_lifetime()
        payload = {
            "data": self._data,
            "expires_at": time.time() + lifetime,
            HASHED_FLAG: True,
        }
        # Write to a temp file then atomically replace to avoid partial reads
        write_payload(self._path, path, payload)
        if self._legacy:
            # Migrated: the record named after the raw session ID goes away.
            self._legacy_file(self._id).unlink(missing_ok=True)
            self._legacy = False
        return True

    def regenerate(self) -> str:
        """Invalidate old session file and assign a new ID."""
        if self._id:
            self._file(self._id).unlink(missing_ok=True)
            if self._legacy:
                self._legacy_file(self._id).unlink(missing_ok=True)
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
        """Get a value and remove it from the session in one call."""
        value = self._data.pop(key, default)
        return value

    def remember(self, key: str, callback: Any) -> Any:
        """Return the session value for key; if absent, call callback, store, and return the result."""
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

    def _gc(self) -> None:
        now = time.time()
        for f in self._path.iterdir():
            if f.name.startswith("."):
                continue
            try:
                payload = json.loads(f.read_text())
                if payload.get("expires_at", 0) < now:
                    f.unlink(missing_ok=True)
            except Exception:
                pass

    def _file(self, session_id: str) -> Path:
        """Path of the session's file: named by the hash of the ID."""
        return self._path / storage_key(session_id)

    def _legacy_file(self, session_id: str) -> Path:
        """Where releases before hashed storage kept the session: named by the raw ID."""
        return self._path / session_id

    def _read(self, session_id: str) -> dict[str, Any] | None:
        """Return the stored data, or None when there is no live record."""
        return self._read_file(self._file(session_id), hashed=True)

    def _read_legacy(self, session_id: str) -> dict[str, Any] | None:
        """Return the data of a legacy (raw-ID-named) record, or None."""
        return self._read_file(self._legacy_file(session_id), hashed=False)

    @staticmethod
    def _read_file(f: Path, *, hashed: bool) -> dict[str, Any] | None:
        payload = load_payload(f)
        if payload is None:
            return None
        if is_hashed_payload(payload) is not hashed:
            # Not the kind of record expected under this name: leave it alone.
            return None
        try:
            expired = float(payload.get("expires_at", 0)) < time.time()
        except (TypeError, ValueError):
            expired = True
        if expired:
            f.unlink(missing_ok=True)
            return None
        data = payload.get("data", {})
        return data if isinstance(data, dict) else {}
