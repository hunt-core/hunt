"""Shared fixtures and helpers for the tests/unit/test_fix_admin_*.py regression tests.

This module holds no tests of its own. It gives the admin fix tests a real
SQLite database (a file under ``tmp_path``), a clean ``Admin`` singleton, real
``Request`` objects and a way to send them through the real router and kernel.
"""

from __future__ import annotations

import importlib
import json
from typing import Any
from urllib.parse import urlencode

import pytest

from hunt.http.middleware import Middleware
from hunt.http.request import Request

CSRF = "test-csrf-token"


class FakeSession:
    """Minimal stand-in for the session store the admin controllers use."""

    def __init__(self, token: str | None = CSRF) -> None:
        self.data: dict[str, Any] = {}
        if token is not None:
            self.data["_csrf_token"] = token
        self.flashed: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value

    def flash(self, key: str, value: Any) -> None:
        self.flashed[key] = value

    def all_flash(self) -> dict[str, Any]:
        return {}

    def csrf_token(self) -> str:
        return str(self.data.get("_csrf_token", ""))


class AttachSession(Middleware):
    """Global middleware that gives every request the same fake session."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def handle(self, request: Request, next: Any) -> Any:
        request._session = self.session  # type: ignore[attr-defined]
        return await next(request)


def make_request(
    method: str = "GET",
    path: str = "/",
    *,
    query: dict | str | None = None,
    form: dict | None = None,
    json_body: Any = None,
    multipart: dict | None = None,
    headers: dict | None = None,
    session: FakeSession | None = None,
    expects_json: bool = False,
) -> Request:
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs["content-type"] = "application/json"
    elif multipart is not None:
        # What the admin create/edit forms send: blank fields are present with an empty value.
        boundary = "----huntadmintestboundary"
        parts = []
        for name, value in multipart.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n')
        parts.append(f"--{boundary}--\r\n")
        body = "".join(parts).encode()
        hdrs["content-type"] = f"multipart/form-data; boundary={boundary}"
    elif form is not None:
        body = urlencode(form, doseq=True).encode()
        hdrs["content-type"] = "application/x-www-form-urlencoded"
    if expects_json:
        hdrs.setdefault("accept", "application/json")
    query_string = query if isinstance(query, str) else urlencode(query or {})
    scope = {
        "type": "http",
        "method": method.upper(),
        "path": path,
        "query_string": query_string.encode(),
        "headers": [(k.encode(), str(v).encode()) for k, v in hdrs.items()],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    request = Request(scope, body)
    if session is not None:
        request._session = session  # type: ignore[attr-defined]
    return request


def body_of(response: Any) -> str:
    body = response._body
    return body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else str(body)


def json_of(response: Any) -> Any:
    return json.loads(body_of(response))


def make_kernel(router: Any, session: FakeSession | None = None, extra_global: list | None = None) -> Any:
    from hunt.http.kernel import HttpKernel

    global_middleware: list = []
    if session is not None:
        global_middleware.append(AttachSession(session))
    global_middleware.extend(extra_global or [])
    return HttpKernel(router, global_middleware=global_middleware)


async def send(kernel: Any, request: Request) -> Any:
    """Run a request through routing, global and route middleware, and the controller."""
    return await kernel._handle(request)


def run_sql(sql: str, bindings: dict | None = None) -> Any:
    from hunt.database.connection import raw

    return raw(sql, bindings or {})


def rows(sql: str, bindings: dict | None = None) -> list[dict]:
    from sqlalchemy import text

    from hunt.database.connection import connection

    with connection().connect() as conn:
        result = conn.execute(text(sql), bindings or {})
        keys = list(result.keys())
        return [dict(zip(keys, r, strict=False)) for r in result.fetchall()]


@pytest.fixture
def admin_db(tmp_path, monkeypatch):
    """Point the default connection at a fresh SQLite file for the duration of a test."""
    conn = importlib.import_module("hunt.database.connection")
    audit = importlib.import_module("hunt.admin.audit")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    saved_connections = dict(conn._connections)
    saved_config = conn._config
    conn._connections.clear()
    conn.configure(
        {
            "default": "sqlite",
            "connections": {"sqlite": {"driver": "sqlite", "database": str(tmp_path / "admin_test.sqlite")}},
        }
    )
    saved_table_flag = audit._TABLE_CREATED
    audit._TABLE_CREATED = False
    try:
        yield conn
    finally:
        for engine in conn._connections.values():
            try:
                engine.dispose()
            except Exception:
                pass
        conn._connections.clear()
        conn._connections.update(saved_connections)
        conn._config = saved_config
        audit._TABLE_CREATED = saved_table_flag


@pytest.fixture
def admin():
    """The Admin singleton with its registrations cleared, restored afterwards."""
    from hunt.admin.application import Admin

    names = (
        "_resources",
        "_dashboard_cards",
        "_tools",
        "_gate",
        "_tool_rules",
        "_tools_gate",
        "_nav",
        "_router",
        "prefix",
        "media_disk",
        "media_path",
        "media_max_kb",
        "audit_strict",
        "audit_tools",
    )
    saved = {name: getattr(Admin, name) for name in names}
    Admin._resources = []
    Admin._dashboard_cards = []
    Admin._tools = []
    Admin._gate = None
    Admin._tool_rules = {}
    Admin._tools_gate = None
    Admin._nav = None
    Admin._router = None
    Admin.prefix = "/hunt-admin"
    Admin.audit_strict = False
    Admin.audit_tools = True
    try:
        yield Admin
    finally:
        for name, value in saved.items():
            setattr(Admin, name, value)
