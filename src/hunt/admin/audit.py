from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Any, ClassVar

_log = logging.getLogger(__name__)

_TABLE_CREATED = False

# Attribute names that never belong in an audit row, whatever the model says.
_ALWAYS_SENSITIVE = frozenset(
    {
        "password",
        "password_hash",
        "remember_token",
        "two_factor_secret",
        "two_factor_recovery_codes",
        "api_token",
    }
)


def sensitive_attributes(resource: Any = None, model: Any = None) -> set[str]:
    """Attribute names to strip from audit rows and exports for ``resource``.

    Combines ``Model.hidden``, every field flagged ``sensitive`` (``Password``)
    and a small built-in deny-list.
    """
    names: set[str] = set(_ALWAYS_SENSITIVE)
    if model is None and resource is not None:
        model = getattr(resource, "model", None)
    hidden = getattr(model, "hidden", None)
    if isinstance(hidden, (list, tuple, set, frozenset)):
        names.update(str(h) for h in hidden)
    if resource is not None:
        try:
            for field in resource.fields():
                if getattr(field, "sensitive", False) is True:
                    attribute = getattr(field, "attribute", None)
                    if isinstance(attribute, str):
                        names.add(attribute)
        except Exception:
            pass
    return names


def audit_safe(resource: Any, attributes: Any) -> dict:
    """Copy ``attributes`` without hidden or sensitive keys."""
    if not isinstance(attributes, dict):
        return {}
    blocked = sensitive_attributes(resource)
    return {k: v for k, v in attributes.items() if k not in blocked}


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
    """Insert one audit row. ``old_data``/``new_data`` must already be sanitised (see ``audit_safe``)."""
    global _TABLE_CREATED
    try:
        diff: dict = {}
        if action in ("update", "toggle"):
            all_keys = set(list(old_data.keys()) + list(new_data.keys()))
            for key in all_keys:
                old_val = old_data.get(key)
                new_val = new_data.get(key)
                if str(old_val) != str(new_val):
                    diff[key] = {"old": old_val, "new": new_val}

        row = {
            "id": str(uuid.uuid4()),
            "uid": str(user_id) if user_id is not None else None,
            "rtype": resource_type,
            "rid": str(record_id),
            "action": action,
            "old": json.dumps(old_data, default=str),
            "new": json.dumps(new_data, default=str),
            "diff": json.dumps(diff, default=str),
            "ts": int(time.time()),
        }
        _ensure_audit_table()
        try:
            _insert_audit_row(row)
        except Exception:
            # The table may have been dropped (or a different database is now
            # configured) since it was last created in this process: retry once.
            _TABLE_CREATED = False
            _ensure_audit_table()
            _insert_audit_row(row)
    except Exception:
        # An audit trail with silent holes is worse than a visible failure.
        _log.exception(
            "Admin audit log write failed (resource=%s record=%s action=%s)", resource_type, record_id, action
        )
        if _strict():
            raise


def _insert_audit_row(row: dict) -> None:
    from sqlalchemy import text

    with _db() as conn:
        conn.execute(
            text(
                "INSERT INTO admin_audit_logs "
                "(id, user_id, resource_type, record_id, action, old_data, new_data, diff, created_at) "
                "VALUES (:id, :uid, :rtype, :rid, :action, :old, :new, :diff, :ts)"
            ),
            row,
        )


@contextlib.contextmanager
def _db() -> Iterator[Any]:
    """Yield a connection, joining the caller's transaction when there is one."""
    import importlib

    db = importlib.import_module("hunt.database.connection")
    use_connection = getattr(db, "use_connection", None)
    if use_connection is not None:
        with use_connection() as conn:
            yield conn
        return
    with db.use_connection(db.connection()) as conn:
        yield conn


def _strict() -> bool:
    try:
        from hunt.admin.application import Admin

        return getattr(Admin, "audit_strict", False) is True
    except Exception:
        return False


def audit_tool(tool: str, action: str, record_id: Any = "", detail: dict | None = None) -> None:
    """Record an operation performed through one of the admin system tools."""
    try:
        from hunt.admin.application import Admin

        if getattr(Admin, "audit_tools", True) is not True:
            return
    except Exception:
        return
    _write_audit(
        _current_user_id(), f"tool:{tool}", record_id if record_id not in (None, "") else "-", action, {}, detail or {}
    )


def _current_user_id() -> Any:
    try:
        from hunt.auth.manager import Auth

        user = Auth.user()
        return user._attributes.get("id") if user is not None else None
    except Exception:
        return None


def _read_audit(resource_type: str, record_id: Any, limit: int = 50) -> list[dict]:
    try:
        from sqlalchemy import text

        with _db() as conn:
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


def _ensure_audit_table() -> None:
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    try:
        from sqlalchemy import text

        with _db() as conn:
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
        _TABLE_CREATED = True
    except Exception:
        _log.exception("Could not create the admin_audit_logs table")
