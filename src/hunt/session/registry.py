from __future__ import annotations

import hmac
import re
import time
from typing import Any

from sqlalchemy import inspect, text

from hunt.database.connection import connection, use_connection
from hunt.session.store import storage_key

TABLE = "user_sessions"


_PUBLIC_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def public_id_for(session_id: str) -> str:
    """Return the non-secret identifier of a session: ``sha256(session_id)`` hex.

    The session ID itself is a bearer credential (it is the cookie value) and
    must never be stored, rendered or put in a URL. The public ID is what the
    ``user_sessions.id`` column and the session stores hold instead; it cannot
    be turned back into the cookie.
    """
    return storage_key(session_id)


def _dialect_name() -> str:
    try:
        name = connection().dialect.name
    except Exception:
        return ""
    return name if isinstance(name, str) else ""


def _upsert_sql(dialect: str) -> str:
    """INSERT-or-refresh statement for the session row, per SQL dialect.

    MySQL and MariaDB have no ``ON CONFLICT``; they use ``ON DUPLICATE KEY
    UPDATE``. SQLite (3.24+) and PostgreSQL share the ``ON CONFLICT`` form.
    """
    insert = (
        f"INSERT INTO {TABLE} (id, user_id, guard, ip_address, user_agent, last_active_at)"
        " VALUES (:id, :uid, :guard, :ip, :ua, :ts)"
    )
    if dialect in ("mysql", "mariadb"):
        return insert + " ON DUPLICATE KEY UPDATE last_active_at = :ts2"
    return insert + " ON CONFLICT (id) DO UPDATE SET last_active_at = :ts2"


def _table_missing() -> bool:
    """True only when the database is reachable and has no user_sessions table."""
    try:
        return not inspect(connection()).has_table(TABLE)
    except Exception:
        return False


def _row(keys: list[str], row: Any) -> dict:
    data = dict(zip(keys, row, strict=False))
    stored_id = data.get("id")
    if stored_id is not None:
        # The stored id is already the hash. Only a row written before ids were
        # hashed (and not migrated yet) still holds a raw session id: that one
        # is hashed here so it is never shown.
        data["public_id"] = public_id_for(stored_id) if _is_legacy_raw_id(stored_id) else stored_id
    return data


class SessionRegistry:
    """Database-backed index of active user sessions.

    Stores a lightweight row per session (sha256(session_id) → user_id + metadata).
    The actual session data lives in the configured session driver (file or
    Redis); this table is purely an index used for targeted revocation and the
    admin sessions panel.

    The table must exist before use — run ``hunt session:table`` then
    ``hunt migrate`` to create it. Every method treats a missing table as "no
    sessions registered". Any other database error is raised, except from the
    two listing helpers ``all_sessions()`` and ``count()``.

    Rows returned by ``get()``, ``sessions_for_user()`` and ``all_sessions()``
    are dicts of the table columns plus ``public_id`` (see
    :func:`public_id_for`). The ``id`` column holds ``sha256(session_id)``,
    never the cookie value, and ``public_id`` is that same value.

    ``register()``, ``touch()``, ``deregister()``, ``get()`` and
    ``revoke_session()`` take the *raw* session ID (``session.id``) and hash
    it themselves. ``revoke_by_public_id()`` takes the hash.

    Rows written before ids were hashed hold a raw session ID until they are
    migrated (``hunt session:prune --hash-ids``, or automatically the next
    time that session is touched). While its session is alive such a row is
    recognised by where its session data is stored: its ``public_id`` is then
    the hash of its ``id``, and it can be revoked like any other. Use
    ``public_id`` for anything shown to a person or placed in a URL.
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
        """Record a new authenticated session. Ignored if the user_sessions
        table does not exist; any other database error is raised."""
        now = int(time.time())
        try:
            sql = _upsert_sql(_dialect_name())
            with use_connection(connection()) as conn:
                conn.execute(
                    text(sql),
                    {
                        "id": public_id_for(session_id),
                        "uid": user_id,
                        "guard": guard,
                        "ip": ip_address,
                        "ua": (user_agent or "")[:512],
                        "ts": now,
                        "ts2": now,
                    },
                )
        except Exception:
            if not _table_missing():
                raise

    def deregister(self, session_id: str) -> None:
        """Remove a single session row (called on logout)."""
        try:
            with use_connection(connection()) as conn:
                # The second value only matches a row from before ids were hashed.
                conn.execute(
                    text(f"DELETE FROM {TABLE} WHERE id IN (:id, :legacy)"),
                    {"id": public_id_for(session_id), "legacy": str(session_id)},
                )
        except Exception:
            if not _table_missing():
                raise

    def touch(self, session_id: str) -> None:
        """Update last_active_at for an existing session.

        A row that still holds the raw session ID (written before ids were
        hashed) is rewritten to the hash here.
        """
        hashed = public_id_for(session_id)
        now = int(time.time())
        try:
            with use_connection(connection()) as conn:
                result = conn.execute(
                    text(f"UPDATE {TABLE} SET last_active_at = :ts WHERE id = :id"),
                    {"id": hashed, "ts": now},
                )
                if result.rowcount == 0:
                    conn.execute(
                        text(f"UPDATE {TABLE} SET id = :id, last_active_at = :ts WHERE id = :legacy"),
                        {"id": hashed, "ts": now, "legacy": str(session_id)},
                    )
        except Exception:
            if not _table_missing():
                raise

    def prune(self, max_age: int | None = None) -> int:
        """Delete rows not active for *max_age* seconds (default: the session
        lifetime) together with their session data. Returns the number removed.

        Raises if a session's data cannot be deleted; its row is then kept.
        """
        if max_age is None:
            from hunt.session.store import _session_lifetime

            max_age = _session_lifetime()
        cutoff = int(time.time()) - int(max_age)
        try:
            with use_connection(connection()) as conn:
                result = conn.execute(
                    text(f"SELECT id FROM {TABLE} WHERE last_active_at < :cutoff"), {"cutoff": cutoff}
                )
                stored_ids = [row[0] for row in result.fetchall()]
        except Exception:
            if not _table_missing():
                raise
            return 0
        removed = 0
        for stored_id in stored_ids:
            _delete_session_data(stored_id)
            # Re-check the age: the session may have been touched meanwhile.
            with use_connection(connection()) as conn:
                result = conn.execute(
                    text(f"DELETE FROM {TABLE} WHERE id = :id AND last_active_at < :cutoff"),
                    {"id": stored_id, "cutoff": cutoff},
                )
                count = result.rowcount
            removed += count if isinstance(count, int) and count > 0 else 0
        return removed

    # ------------------------------------------------------------------
    # Revocation
    # ------------------------------------------------------------------

    def revoke_for_user(self, user_id: Any, guard: str | None = None) -> int:
        """Delete all sessions for a user from both the registry and the
        session driver.  Returns the number of sessions revoked.

        Raises if the registry cannot be read or a session cannot be deleted:
        a revocation that did nothing must not look like one that worked. The
        registry rows are removed only after the session data is gone.
        """
        rows = self.sessions_for_user(user_id, guard=guard)
        for row in rows:
            _delete_session_data(row["id"])
        if not rows:
            return 0
        with use_connection(connection()) as conn:
            if guard:
                conn.execute(
                    text(f"DELETE FROM {TABLE} WHERE user_id = :uid AND guard = :g"),
                    {"uid": user_id, "g": guard},
                )
            else:
                conn.execute(
                    text(f"DELETE FROM {TABLE} WHERE user_id = :uid"),
                    {"uid": user_id},
                )
        return len(rows)

    def revoke_session(self, session_id: str) -> bool:
        """Revoke a single session by its raw ID. Returns True if it existed."""
        row = self.get(session_id)
        if row is None:
            return False
        _delete_session_data(row["id"])
        self.deregister(session_id)
        return True

    def revoke_by_public_id(self, public_id: str) -> bool:
        """Revoke the session whose ``public_id`` is given. Returns True if it existed."""
        row = self.find_by_public_id(public_id)
        if row is None:
            return False
        _delete_session_data(row["id"])
        with use_connection(connection()) as conn:
            conn.execute(text(f"DELETE FROM {TABLE} WHERE id = :id"), {"id": row["id"]})
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> dict | None:
        """Return the row of the session with the given raw ID, or None."""
        row = self._get_stored(public_id_for(session_id))
        if row is None:
            # A row from before ids were hashed.
            row = self._get_stored(str(session_id))
        return row

    def _get_stored(self, stored_id: str) -> dict | None:
        """Return the row whose ``id`` column equals *stored_id*, or None."""
        try:
            with use_connection(connection()) as conn:
                result = conn.execute(
                    text(f"SELECT * FROM {TABLE} WHERE id = :id"),
                    {"id": stored_id},
                )
                keys = list(result.keys())
                row = result.fetchone()
        except Exception:
            if not _table_missing():
                raise
            return None
        if row is None:
            return None
        return _row(keys, row)

    def find_by_public_id(self, public_id: str) -> dict | None:
        """Return the row whose ``public_id`` matches, or None.

        Normally a primary-key lookup, since the ``id`` column is the public
        ID. Only when that finds nothing are the remaining ids compared by
        hash, to reach a row that still holds a raw session ID. A raw session
        ID is never accepted in place of a public ID.
        """
        if not isinstance(public_id, str) or not _PUBLIC_ID_RE.match(public_id):
            return None
        row = self._get_stored(public_id)
        if row is not None and hmac.compare_digest(str(row.get("public_id")), public_id):
            return row
        try:
            with use_connection(connection()) as conn:
                result = conn.execute(text(f"SELECT id FROM {TABLE}"))
                ids = [row[0] for row in result.fetchall()]
        except Exception:
            if not _table_missing():
                raise
            return None
        for stored_id in ids:
            if hmac.compare_digest(public_id_for(stored_id), public_id):
                row = self._get_stored(stored_id)
                if row is not None and hmac.compare_digest(str(row.get("public_id")), public_id):
                    return row
        return None

    def sessions_for_user(self, user_id: Any, guard: str | None = None) -> list[dict]:
        try:
            with use_connection(connection()) as conn:
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
                return [_row(keys, row) for row in result.fetchall()]
        except Exception:
            if not _table_missing():
                raise
            return []

    def all_sessions(self, page: int = 1, per_page: int = 25) -> tuple[list[dict], int]:
        """Return (rows, total_count) for the admin panel."""
        try:
            page = max(1, int(page))
            per_page = max(1, int(per_page))
        except (TypeError, ValueError):
            page, per_page = 1, 25
        offset = (page - 1) * per_page
        try:
            with use_connection(connection()) as conn:
                count_result = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE}"))
                total = count_result.fetchone()[0] or 0
                result = conn.execute(
                    text(f"SELECT * FROM {TABLE} ORDER BY last_active_at DESC LIMIT :lim OFFSET :off"),
                    {"lim": per_page, "off": offset},
                )
                keys = list(result.keys())
                rows = [_row(keys, row) for row in result.fetchall()]
            return rows, total
        except Exception:
            return [], 0

    def count(self) -> int:
        try:
            with use_connection(connection()) as conn:
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


def _sessions_dir() -> Any:
    from pathlib import Path

    from hunt.support.helpers import storage_path

    return Path(storage_path("framework", "sessions"))


def _is_legacy_raw_id(stored_id: Any) -> bool:
    """True when a ``user_sessions.id`` value is a raw session ID, not a hash.

    Both are 64 hex characters, so the value alone cannot tell. The session
    data can: a raw ID *R* has its data either still under the legacy raw key
    (a file named *R* without the ``hashed`` flag, or ``hunt:session:R``) or,
    once the store has migrated it, under ``sha256(R)``. A hash has neither.
    A row whose session data is gone is reported as not legacy: whatever its
    ``id`` is, no session can be started with it.
    """
    stored_id = str(stored_id)
    if not _PUBLIC_ID_RE.match(stored_id):
        return False
    from hunt.session import session_driver

    if session_driver() == "redis":
        from hunt.redis_connection import get_redis
        from hunt.session.redis_store import hashed_key, legacy_key

        return bool(get_redis().exists(legacy_key(stored_id), hashed_key(storage_key(stored_id))))

    from hunt.session.store import is_hashed_payload, load_payload

    try:
        directory = _sessions_dir()
    except RuntimeError:
        # No bootstrapped application, so no session storage to look in.
        return False
    payload = load_payload(directory / stored_id)
    if payload is not None and not is_hashed_payload(payload):
        return True
    migrated = load_payload(directory / storage_key(stored_id))
    return migrated is not None and is_hashed_payload(migrated)


def _delete_session_file(stored_id: str) -> None:
    # The ID becomes a file name: refuse anything that is not a session ID.
    stored_id = str(stored_id)
    if not _PUBLIC_ID_RE.match(stored_id):
        return
    directory = _sessions_dir()
    # The session's file is named by the stored hash. The second name is where
    # the store puts the data of a session whose row still holds a raw ID.
    (directory / stored_id).unlink(missing_ok=True)
    (directory / storage_key(stored_id)).unlink(missing_ok=True)


def _delete_session_redis(stored_id: str) -> None:
    from hunt.redis_connection import get_redis
    from hunt.session.redis_store import hashed_key, legacy_key

    stored_id = str(stored_id)
    # Hashed key of the stored hash, plus the two places the data of a row
    # that still holds a raw ID can be: its legacy key and its migrated key.
    get_redis().delete(hashed_key(stored_id), legacy_key(stored_id), hashed_key(storage_key(stored_id)))


def _delete_session_data(stored_id: str) -> None:
    """Delete a session's data from file storage or Redis, given the value of
    its ``user_sessions.id`` column (the hash of the session ID).

    Errors propagate: the caller is revoking a session and must learn that it
    is still alive.
    """
    from hunt.session import session_driver

    driver = session_driver()
    if driver == "redis":
        _delete_session_redis(stored_id)
    else:
        _delete_session_file(stored_id)
