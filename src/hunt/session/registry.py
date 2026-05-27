from __future__ import annotations

import os
import time
from typing import Any

from sqlalchemy import text

from hunt.database.connection import connection

TABLE = "user_sessions"


class SessionRegistry:
    """Database-backed index of active user sessions.

    Stores a lightweight row per session (session_id → user_id + metadata).
    The actual session data lives in the configured session driver (file or
    Redis); this table is purely an index used for targeted revocation and the
    admin sessions panel.

    The table must exist before use — run ``hunt session:table`` then
    ``hunt migrate`` to create it.
    """

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def register(
        self,
        session_id: str,
        user_id: Any,
        guard: str = "web",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Record a new authenticated session.  Silently ignored if the
        user_sessions table does not yet exist."""
        now = int(time.time())
        try:
            with connection().connect() as conn:
                conn.execute(
                    text(
                        f"INSERT INTO {TABLE} (id, user_id, guard, ip_address, user_agent, last_active_at)"
                        " VALUES (:id, :uid, :guard, :ip, :ua, :ts)"
                        " ON CONFLICT (id) DO UPDATE SET last_active_at = :ts"
                    ),
                    {
                        "id": session_id,
                        "uid": user_id,
                        "guard": guard,
                        "ip": ip_address,
                        "ua": (user_agent or "")[:512],
                        "ts": now,
                    },
                )
                conn.commit()
        except Exception:
            pass

    def deregister(self, session_id: str) -> None:
        """Remove a single session row (called on logout)."""
        try:
            with connection().connect() as conn:
                conn.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": session_id})
                conn.commit()
        except Exception:
            pass

    def touch(self, session_id: str) -> None:
        """Update last_active_at for an existing session."""
        try:
            with connection().connect() as conn:
                conn.execute(
                    text(f"UPDATE {TABLE} SET last_active_at = :ts WHERE id = :id"),
                    {"id": session_id, "ts": int(time.time())},
                )
                conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    def revoke_for_user(self, user_id: Any, guard: str | None = None) -> int:
        """Delete all sessions for a user from both the registry and the
        session driver.  Returns the number of sessions revoked."""
        rows = self.sessions_for_user(user_id, guard=guard)
        for row in rows:
            _delete_session_data(row["id"])
        if not rows:
            return 0
        try:
            if guard:
                with connection().connect() as conn:
                    conn.execute(
                        text(f"DELETE FROM {TABLE} WHERE user_id = :uid AND guard = :g"),
                        {"uid": user_id, "g": guard},
                    )
                    conn.commit()
            else:
                with connection().connect() as conn:
                    conn.execute(
                        text(f"DELETE FROM {TABLE} WHERE user_id = :uid"),
                        {"uid": user_id},
                    )
                    conn.commit()
        except Exception:
            pass
        return len(rows)

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a single session by ID. Returns True if it existed."""
        row = self.get(session_id)
        if row is None:
            return False
        _delete_session_data(session_id)
        self.deregister(session_id)
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> dict | None:
        try:
            with connection().connect() as conn:
                result = conn.execute(
                    text(f"SELECT * FROM {TABLE} WHERE id = :id"),
                    {"id": session_id},
                )
                keys = list(result.keys())
                row = result.fetchone()
            if row is None:
                return None
            return dict(zip(keys, row, strict=False))
        except Exception:
            return None

    def sessions_for_user(self, user_id: Any, guard: str | None = None) -> list[dict]:
        try:
            with connection().connect() as conn:
                if guard:
                    result = conn.execute(
                        text(f"SELECT * FROM {TABLE} WHERE user_id = :uid AND guard = :g ORDER BY last_active_at DESC"),
                        {"uid": user_id, "g": guard},
                    )
                else:
                    result = conn.execute(
                        text(f"SELECT * FROM {TABLE} WHERE user_id = :uid ORDER BY last_active_at DESC"),
                        {"uid": user_id},
                    )
                keys = list(result.keys())
                return [dict(zip(keys, row, strict=False)) for row in result.fetchall()]
        except Exception:
            return []

    def all_sessions(self, page: int = 1, per_page: int = 25) -> tuple[list[dict], int]:
        """Return (rows, total_count) for the admin panel."""
        offset = (page - 1) * per_page
        try:
            with connection().connect() as conn:
                count_result = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}"))
                total = count_result.fetchone()[0] or 0
                result = conn.execute(
                    text(f"SELECT * FROM {TABLE} ORDER BY last_active_at DESC LIMIT :lim OFFSET :off"),
                    {"lim": per_page, "off": offset},
                )
                keys = list(result.keys())
                rows = [dict(zip(keys, row, strict=False)) for row in result.fetchall()]
            return rows, total
        except Exception:
            return [], 0

    def count(self) -> int:
        try:
            with connection().connect() as conn:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}"))
                return result.fetchone()[0] or 0
        except Exception:
            return 0


# ------------------------------------------------------------------
# Module-level helper
# ------------------------------------------------------------------


def revoke_sessions_for(user_id: Any, guard: str | None = None) -> int:
    """Revoke all active sessions for a user.

    Removes sessions from both the registry table and the underlying
    session driver (file or Redis).  Returns the number revoked.

    Typical usage after a password reset::

        if Password.reset(credentials):
            revoke_sessions_for(user.id)
    """
    return SessionRegistry().revoke_for_user(user_id, guard=guard)


# ------------------------------------------------------------------
# Internals
# ------------------------------------------------------------------


def _delete_session_file(session_id: str) -> None:
    from pathlib import Path

    from hunt.support.helpers import storage_path

    f = Path(storage_path("framework", "sessions")) / session_id
    f.unlink(missing_ok=True)


def _delete_session_redis(session_id: str) -> None:
    from hunt.redis_connection import get_redis

    get_redis().delete(f"hunt:session:{session_id}")


def _delete_session_data(session_id: str) -> None:
    """Delete the raw session data from file storage or Redis."""
    driver = os.environ.get("SESSION_DRIVER", "file").lower()
    try:
        if driver == "redis":
            _delete_session_redis(session_id)
        else:
            _delete_session_file(session_id)
    except Exception:
        pass
