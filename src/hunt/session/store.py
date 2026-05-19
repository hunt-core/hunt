from __future__ import annotations

import json
import os
import random
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_SESSION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class FileSessionStore:
    """File-backed session store. One JSON file per session ID."""

    LIFETIME = 7200  # 2 hours

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        self._id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, session_id: str) -> None:
        if not _SESSION_ID_RE.match(session_id):
            # Reject malformed/path-traversal IDs; start a fresh session
            session_id = os.urandom(32).hex()
        self._id = session_id
        self._data = self._read(session_id)
        if random.random() < 0.01:
            self._gc()

    def age_flash(self) -> None:
        """Promote new flash data to old so it's readable this request."""
        self._data["_flash_old"] = self._data.pop("_flash_new", {})

    def save(self) -> None:
        if not self._id:
            return
        payload = {
            "data": self._data,
            "expires_at": time.time() + self.LIFETIME,
        }
        path = self._file(self._id)
        # Write to a temp file then atomically replace to avoid partial reads
        fd, tmp_path = tempfile.mkstemp(dir=self._path, prefix=".sess_")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(payload))
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def regenerate(self) -> str:
        """Invalidate old session file and assign a new ID."""
        if self._id:
            self._file(self._id).unlink(missing_ok=True)
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
        return self._path / session_id

    def _read(self, session_id: str) -> dict[str, Any]:
        f = self._file(session_id)
        if not f.exists():
            return {}
        try:
            payload = json.loads(f.read_text())
            if payload.get("expires_at", 0) < time.time():
                f.unlink(missing_ok=True)
                return {}
            return payload.get("data", {})
        except Exception:
            return {}
