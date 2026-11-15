from __future__ import annotations

import contextlib
import json
from typing import Any, ClassVar
from unittest.mock import patch

from hunt.http.kernel import HttpKernel
from hunt.http.request import Request
from hunt.http.response import Response


class TestResponse:
    def __init__(self, response: Response, body: bytes) -> None:
        self._response = response
        self._body = body

    @property
    def status_code(self) -> int:
        return self._response.status

    @property
    def content(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def json(self, key: str | None = None) -> Any:
        data = json.loads(self._body)
        if key is not None:
            return data.get(key)
        return data

    # ------------------------------------------------------------------
    # Fluent assertions
    # ------------------------------------------------------------------

    def assert_status(self, status: int) -> TestResponse:
        assert self._response.status == status, (
            f"Expected status {status}, got {self._response.status}. Body: {self.content[:200]}"
        )
        return self

    def assert_ok(self) -> TestResponse:
        return self.assert_status(200)

    def assert_created(self) -> TestResponse:
        return self.assert_status(201)

    def assert_no_content(self) -> TestResponse:
        return self.assert_status(204)

    def assert_not_found(self) -> TestResponse:
        return self.assert_status(404)

    def assert_forbidden(self) -> TestResponse:
        return self.assert_status(403)

    def assert_unauthorized(self) -> TestResponse:
        return self.assert_status(401)

    def assert_unprocessable(self) -> TestResponse:
        return self.assert_status(422)

    def assert_redirect(self, url: str | None = None) -> TestResponse:
        assert self._response.status in (301, 302, 303, 307, 308), f"Expected redirect, got {self._response.status}"
        if url:
            location = dict(self._response._headers).get("Location", "")
            assert location == url, f"Expected redirect to {url}, got {location}"
        return self

    def assert_json(self, key: str | None = None, value: Any = None) -> TestResponse:
        data = self.json()
        if key is None:
            assert isinstance(data, (dict, list)), f"Response is not JSON: {self.content[:200]}"
            return self
        actual = data.get(key) if isinstance(data, dict) else None
        assert actual == value, f"Expected JSON['{key}'] = {value!r}, got {actual!r}"
        return self

    def assert_json_structure(self, keys: list[str]) -> TestResponse:
        data = self.json()
        for key in keys:
            assert key in data, f"JSON key '{key}' not found. Keys: {list(data.keys())}"
        return self

    def assert_see(self, text: str) -> TestResponse:
        assert text in self.content, f"Expected to see '{text}' in response"
        return self

    def assert_dont_see(self, text: str) -> TestResponse:
        assert text not in self.content, f"Did not expect to see '{text}' in response"
        return self

    def assert_header(self, name: str, value: str | None = None) -> TestResponse:
        headers = {k.lower(): v for k, v in self._response._headers.items()}
        ct_lower = self._response.content_type.lower() if hasattr(self._response, "content_type") else ""
        assert name.lower() in headers or name.lower() == "content-type", f"Header '{name}' not found"
        if value is not None:
            actual = headers.get(name.lower(), ct_lower)
            assert value in actual, f"Header '{name}': expected '{value}', got '{actual}'"
        return self

    def assert_cookie(self, name: str, value: str | None = None) -> TestResponse:
        cookies: dict[str, str] = {}
        for raw in self._response._cookie_headers:
            parts = raw.split(";")
            pair = parts[0].strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                cookies[k.strip()] = v.strip()
        assert name in cookies, f"Cookie '{name}' not found in response"
        if value is not None:
            assert cookies[name] == value, f"Cookie '{name}': expected '{value}', got '{cookies[name]}'"
        return self

    def assert_json_count(self, key: str | None = None, count: int = 0) -> TestResponse:
        data = self.json()
        target = data.get(key) if key and isinstance(data, dict) else data
        assert isinstance(target, (list, dict)), f"Expected a list/dict at '{key}', got {type(target).__name__}"
        assert len(target) == count, f"Expected {count} items at '{key}', got {len(target)}"
        return self

    def assert_json_missing(self, key: str) -> TestResponse:
        data = self.json()
        assert isinstance(data, dict) and key not in data, f"Expected JSON key '{key}' to be absent"
        return self

    def assert_json_fragment(self, fragment: dict) -> TestResponse:
        data = self.json()
        assert isinstance(data, dict), "Response is not a JSON object"
        for k, v in fragment.items():
            assert k in data and data[k] == v, f"JSON fragment mismatch at '{k}': expected {v!r}, got {data.get(k)!r}"
        return self


class HuntTestCase:
    """Base test class providing HTTP test helpers."""

    kernel: HttpKernel | None = None
    _acting_as_user: Any = None
    _acting_as_guard: str = "web"
    _skip_middleware: set | None = None

    def acting_as(self, user: Any, guard: str = "web") -> HuntTestCase:
        """Set the authenticated user for subsequent requests in this test."""
        self._acting_as_user = user
        self._acting_as_guard = guard
        return self

    def without_middleware(self, *classes: type) -> HuntTestCase:
        """Skip the given middleware classes for subsequent requests in this test."""
        self._skip_middleware = set(classes)
        return self

    def _make_scope(self, method: str, path: str, headers: dict | None = None) -> dict:
        from urllib.parse import urlsplit

        parts = urlsplit(path)
        raw_headers = []
        for k, v in (headers or {}).items():
            raw_headers.append((k.lower().encode(), v.encode()))
        return {
            "type": "http",
            "method": method.upper(),
            "path": parts.path or "/",
            "query_string": (parts.query or "").encode(),
            "headers": raw_headers,
            "scheme": "http",
            "server": ("testserver", 80),
        }

    async def _call(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        json_data: Any = None,
        headers: dict | None = None,
    ) -> TestResponse:
        assert self.kernel is not None, "Set HuntTestCase.kernel before making requests"

        hdrs = dict(headers or {})
        body = b""
        if json_data is not None:
            body = json.dumps(json_data).encode()
            hdrs["Content-Type"] = "application/json"
        elif data:
            from urllib.parse import urlencode

            body = urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"

        scope = self._make_scope(method, path, hdrs)
        request = Request(scope, body)

        with contextlib.ExitStack() as stack:
            # Apply acting_as patches
            if self._acting_as_user is not None:
                user = self._acting_as_user
                user_id = (
                    getattr(user, "_attributes", {}).get("id")
                    if hasattr(user, "_attributes")
                    else getattr(user, "id", None)
                )
                from hunt.auth.manager import Auth

                stack.enter_context(patch.object(Auth, "user", return_value=user))
                stack.enter_context(patch.object(Auth, "check", return_value=True))
                stack.enter_context(patch.object(Auth, "guest", return_value=False))
                stack.enter_context(patch.object(Auth, "id", return_value=user_id))

            # Apply without_middleware filtering
            if self._skip_middleware:
                skip = self._skip_middleware
                original_build = self.kernel._build_pipeline

                def _filtered_build(middleware_list: list, handler: Any, _orig=original_build, _skip=skip) -> Any:
                    filtered = [
                        m for m in middleware_list if not (isinstance(m, type) and any(issubclass(m, s) for s in _skip))
                    ]
                    return _orig(filtered, handler)

                self.kernel._build_pipeline = _filtered_build

            try:
                response = await self.kernel._handle(request)
            finally:
                if self._skip_middleware and "_build_pipeline" in self.kernel.__dict__:
                    del self.kernel._build_pipeline

        return TestResponse(response, response._body)

    async def get(self, path: str, headers: dict | None = None) -> TestResponse:
        return await self._call("GET", path, headers=headers)

    async def post(
        self, path: str, data: dict | None = None, json: Any = None, headers: dict | None = None
    ) -> TestResponse:
        return await self._call("POST", path, data=data, json_data=json, headers=headers)

    async def put(
        self, path: str, data: dict | None = None, json: Any = None, headers: dict | None = None
    ) -> TestResponse:
        return await self._call("PUT", path, data=data, json_data=json, headers=headers)

    async def patch(
        self, path: str, data: dict | None = None, json: Any = None, headers: dict | None = None
    ) -> TestResponse:
        return await self._call("PATCH", path, data=data, json_data=json, headers=headers)

    async def delete(self, path: str, headers: dict | None = None) -> TestResponse:
        return await self._call("DELETE", path, headers=headers)

    # ------------------------------------------------------------------
    # Database assertions
    # ------------------------------------------------------------------

    def assert_database_has(self, table: str, data: dict) -> None:
        from hunt.database.query_builder import QueryBuilder

        qb = QueryBuilder(table)
        for k, v in data.items():
            qb = qb.where(k, v)
        assert qb.exists(), f"Expected record in '{table}' with {data}"

    def assert_database_missing(self, table: str, data: dict) -> None:
        from hunt.database.query_builder import QueryBuilder

        qb = QueryBuilder(table)
        for k, v in data.items():
            qb = qb.where(k, v)
        assert not qb.exists(), f"Did not expect record in '{table}' with {data}"

    def assert_database_count(self, table: str, count: int) -> None:
        from hunt.database.query_builder import QueryBuilder

        actual = QueryBuilder(table).count()
        assert actual == count, f"Expected {count} records in '{table}', found {actual}"


class DatabaseTransactions:
    """Mixin: wrap each test in a DB transaction that is rolled back on teardown.

    Faster than ``RefreshDatabase`` for large tables — no DELETE statements are
    issued; the transaction is simply rolled back.  Works with SQLAlchemy
    synchronous engines (SQLite, MySQL, PostgreSQL).

    Everything the test does through the framework (Model, QueryBuilder,
    Schema, ``raw()``, including ``async_*`` calls that run in worker threads)
    uses the test's connection, so nothing it writes is committed. A
    ``transaction()`` / ``begin()`` block inside the code under test becomes a
    savepoint of the test's transaction.

    Not covered: code that takes ``connection()`` and opens its own
    ``engine.connect()``, and MySQL DDL (which commits implicitly).

    Set ``transaction_connections`` to wrap named connections instead of
    (or as well as) the default one (``None``).

    Usage::

        class MyTest(HuntTestCase, DatabaseTransactions):
            ...
    """

    transaction_connections: ClassVar[list[str | None]] = [None]

    _db_connection: Any = None
    _db_transaction: Any = None
    _db_pins: list | None = None

    def setup_method(self) -> None:
        from hunt.database.connection import _pin_connection

        self._db_pins = []
        try:
            for name in self.transaction_connections:
                self._db_pins.append(_pin_connection(name))
        except BaseException:
            self.teardown_method()
            raise
        if self._db_pins:
            _, self._db_connection, self._db_transaction = self._db_pins[0]

    def teardown_method(self) -> None:
        from hunt.database.connection import _unpin_connection

        pins, self._db_pins = self._db_pins or [], []
        self._db_connection = None
        self._db_transaction = None
        errors: list[BaseException] = []
        for engine, conn, tx in reversed(pins):
            try:
                _unpin_connection(engine, conn, tx)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]


class RefreshDatabase:
    """Mixin: delete all rows from every table after each test.

    Override ``refresh_tables`` to limit which tables are cleaned; otherwise
    every table is purged except the migrations tracking table. Tables are
    emptied children-first (in foreign-key order) and a failed DELETE fails
    the test instead of silently leaving rows behind.

    Usage::

        class MyTest(HuntTestCase, RefreshDatabase):
            refresh_tables = ["users", "posts"]
    """

    refresh_tables: ClassVar[list[str]] = []

    def setup_method(self) -> None:
        pass

    def teardown_method(self) -> None:
        from sqlalchemy import MetaData, inspect, text

        from hunt.database.connection import connection
        from hunt.database.schema.migration import MIGRATIONS_TABLE

        engine = connection()
        quote = engine.dialect.identifier_preparer.quote
        explicit = list(self.refresh_tables)
        existing = [t for t in inspect(engine).get_table_names() if t != MIGRATIONS_TABLE]

        # Children before parents, so a DELETE never trips a foreign key.
        metadata = MetaData()
        metadata.reflect(bind=engine, only=existing)
        ordered = [t.name for t in reversed(metadata.sorted_tables)]
        ordered += [t for t in existing if t not in ordered]

        if explicit:
            wanted = set(explicit)
            tables = [t for t in ordered if t in wanted]
            # A listed table that does not exist is reported, not skipped.
            tables += [t for t in explicit if t not in set(tables)]
        else:
            tables = ordered

        with engine.connect() as conn:
            try:
                for table in tables:
                    conn.execute(text(f"DELETE FROM {quote(table)}"))
            except Exception:
                conn.rollback()
                raise
            conn.commit()
