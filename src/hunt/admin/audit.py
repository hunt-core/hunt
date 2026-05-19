from __future__ import annotations

import json
import time
import uuid
from typing import Any, ClassVar


class AuditLog:
    """Mixin for AdminResource subclasses that records create/update/delete events.

    Usage::

        class PostResource(AdminResource, AuditLog):
            model = Post
            ...

    Audit entries are written to the ``admin_audit_logs`` table, which is
    created automatically on first write (SQLite/MySQL/Postgres compatible).
    """

    audit_log: ClassVar[bool] = True

    def audit_log_history(self, resource_type: str, record_id: Any, limit: int = 50) -> list[dict]:
        """Return recent audit entries for the given record."""
        return _read_audit(resource_type, record_id, limit)


def _write_audit(
    user_id: Any,
    resource_type: str,
    record_id: Any,
    action: str,
    old_data: dict,
    new_data: dict,
) -> None:
    try:
        from sqlalchemy import text

        from hunt.database.connection import connection

        _ensure_audit_table()

        diff: dict = {}
        if action == "update":
            all_keys = set(list(old_data.keys()) + list(new_data.keys()))
            for key in all_keys:
                old_val = old_data.get(key)
                new_val = new_data.get(key)
                if str(old_val) != str(new_val):
                    diff[key] = {"old": old_val, "new": new_val}

        with connection().connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO admin_audit_logs "
                    "(id, user_id, resource_type, record_id, action, old_data, new_data, diff, created_at) "
                    "VALUES (:id, :uid, :rtype, :rid, :action, :old, :new, :diff, :ts)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "uid": str(user_id) if user_id is not None else None,
                    "rtype": resource_type,
                    "rid": str(record_id),
                    "action": action,
                    "old": json.dumps(old_data, default=str),
                    "new": json.dumps(new_data, default=str),
                    "diff": json.dumps(diff, default=str),
                    "ts": int(time.time()),
                },
            )
            conn.commit()
    except Exception:
        pass


def _read_audit(resource_type: str, record_id: Any, limit: int = 50) -> list[dict]:
    try:
        from sqlalchemy import text

        from hunt.database.connection import connection

        with connection().connect() as conn:
            result = conn.execute(
                text(
                    "SELECT * FROM admin_audit_logs "
                    "WHERE resource_type = :rtype AND record_id = :rid "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"rtype": resource_type, "rid": str(record_id), "lim": limit},
            )
            keys = list(result.keys())
            rows = result.fetchall()
    except Exception:
        return []

    entries = []
    for row in rows:
        d = dict(zip(keys, row, strict=False))
        for field in ("old_data", "new_data", "diff"):
            if field in d and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except Exception:
                    pass
        entries.append(d)
    return entries


_TABLE_CREATED = False


def _ensure_audit_table() -> None:
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    try:
        from sqlalchemy import text

        from hunt.database.connection import connection

        with connection().connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS admin_audit_logs ("
                    "id TEXT PRIMARY KEY, "
                    "user_id TEXT, "
                    "resource_type TEXT NOT NULL, "
                    "record_id TEXT NOT NULL, "
                    "action TEXT NOT NULL, "
                    "old_data TEXT, "
                    "new_data TEXT, "
                    "diff TEXT, "
                    "created_at INTEGER NOT NULL"
                    ")"
                )
            )
            conn.commit()
        _TABLE_CREATED = True
    except Exception:
        pass
