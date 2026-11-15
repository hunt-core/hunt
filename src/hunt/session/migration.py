from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from hunt.database.connection import connection, use_connection
from hunt.session.registry import _PUBLIC_ID_RE, TABLE, _is_legacy_raw_id, _sessions_dir, _table_missing
from hunt.session.store import HASHED_FLAG, is_hashed_payload, load_payload, storage_key, write_payload


def hash_session_ids() -> dict[str, int]:
    """Rewrite session identifiers stored by earlier releases to their hashes.

    Earlier releases stored the raw session ID (the cookie value) as the
    ``user_sessions.id`` column, as the session file name and in the Redis
    key. This replaces each of them with ``sha256(session_id)``:

    * ``user_sessions`` rows that still hold a raw ID get the hash instead;
    * session files named after a raw ID are rewritten under the hashed name;
    * Redis keys ``hunt:session:<id>`` are renamed to the hashed key.

    Sessions stay valid: the cookie is unchanged, only where its data is kept
    moves. Safe to run repeatedly and while the application is serving
    requests; a second run finds nothing to do.

    Returns ``{"rows": <registry rows rewritten>, "sessions": <session records moved>}``.
    """
    from hunt.session import session_driver

    # Rows first: a raw row is recognised by where its session data lives, and
    # that stays recognisable after the data has moved, so a run interrupted
    # at any point is completed by the next one.
    rows = _hash_registry_rows()
    sessions = _hash_redis_keys() if session_driver() == "redis" else _hash_session_files(_sessions_dir())
    return {"rows": rows, "sessions": sessions}


def _hash_registry_rows() -> int:
    try:
        with use_connection(connection()) as conn:
            result = conn.execute(text(f"SELECT id FROM {TABLE}"))
            stored_ids = [row[0] for row in result.fetchall()]
    except Exception:
        if not _table_missing():
            raise
        return 0
    changed = 0
    for stored_id in stored_ids:
        if not _is_legacy_raw_id(stored_id):
            continue
        hashed = storage_key(stored_id)
        with use_connection(connection()) as conn:
            exists = conn.execute(text(f"SELECT 1 FROM {TABLE} WHERE id = :id"), {"id": hashed}).fetchone()
            if exists is not None:
                # Already registered under the hash: the raw row is a leftover.
                conn.execute(text(f"DELETE FROM {TABLE} WHERE id = :legacy"), {"legacy": stored_id})
            else:
                conn.execute(
                    text(f"UPDATE {TABLE} SET id = :id WHERE id = :legacy"), {"id": hashed, "legacy": stored_id}
                )
        changed += 1
    return changed


def _hash_session_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    moved = 0
    now = time.time()
    for legacy in sorted(directory.iterdir()):
        if not _PUBLIC_ID_RE.match(legacy.name):
            continue
        payload = load_payload(legacy)
        if payload is None or is_hashed_payload(payload):
            continue
        try:
            expired = float(payload.get("expires_at", 0)) < now
        except (TypeError, ValueError):
            expired = True
        if expired:
            legacy.unlink(missing_ok=True)
            continue
        target = directory / storage_key(legacy.name)
        payload[HASHED_FLAG] = True
        # Never overwrite: a request may have migrated (and updated) this
        # session since the file was read.
        if not write_payload(directory, target, payload, exclusive=True):
            legacy.unlink(missing_ok=True)  # a stale leftover of an already migrated session
            continue
        if not legacy.exists():
            # Revoked or logged out while it was being copied: do not bring it back.
            target.unlink(missing_ok=True)
            continue
        legacy.unlink(missing_ok=True)
        moved += 1
    return moved


def _hash_redis_keys() -> int:
    from hunt.redis_connection import get_redis
    from hunt.session.redis_store import LEGACY_KEY_PREFIX, hashed_key, is_missing_key_error

    client: Any = get_redis()
    moved = 0
    for key in list(client.scan_iter(match=f"{LEGACY_KEY_PREFIX}*")):
        name = key.decode() if isinstance(key, bytes) else str(key)
        session_id = name[len(LEGACY_KEY_PREFIX) :]
        if not _PUBLIC_ID_RE.match(session_id):
            continue  # hashed keys and anything else in the namespace
        try:
            # Atomic, and keeps the remaining time to live.
            client.rename(name, hashed_key(storage_key(session_id)))
        except Exception as exc:
            if not is_missing_key_error(exc):
                raise
            continue  # expired, revoked or migrated by a request meanwhile
        moved += 1
    return moved
