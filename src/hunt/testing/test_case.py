from __future__ import annotations

import json
from typing import Any

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

    def assert_status(self, status: int) -> "TestResponse":
        assert self._response.status == status, (
            f"Expected status {status}, got {self._response.status}. Body: {self.content[:200]}"
        )
        return self

    def assert_ok(self) -> "TestResponse":
        return self.assert_status(200)

    def assert_created(self) -> "TestResponse":
        return self.assert_status(201)

    def assert_no_content(self) -> "TestResponse":
        return self.assert_status(204)

    def assert_not_found(self) -> "TestResponse":
        return self.assert_status(404)

    def assert_forbidden(self) -> "TestResponse":
        return self.assert_status(403)

    def assert_unauthorized(self) -> "TestResponse":
        return self.assert_status(401)

    def assert_unprocessable(self) -> "TestResponse":
        return self.assert_status(422)

    def assert_redirect(self, url: str | None = None) -> "TestResponse":
        assert self._response.status in (301, 302, 303, 307, 308), (
            f"Expected redirect, got {self._response.status}"
        )
        if url:
            location = dict(self._response._headers).get("Location", "")
            assert location == url, f"Expected redirect to {url}, got {location}"
        return self

    def assert_json(self, key: str | None = None, value: Any = None) -> "TestResponse":
        data = self.json()
        if key is None:
            assert isinstance(data, (dict, list)), f"Response is not JSON: {self.content[:200]}"
            return self
        actual = data.get(key) if isinstance(data, dict) else None
        assert actual == value, f"Expected JSON['{key}'] = {value!r}, got {actual!r}"
        return self

    def assert_json_structure(self, keys: list[str]) -> "TestResponse":
        data = self.json()
        for key in keys:
            assert key in data, f"JSON key '{key}' not found. Keys: {list(data.keys())}"
        return self

    def assert_see(self, text: str) -> "TestResponse":
        assert text in self.content, f"Expected to see '{text}' in response"
        return self

    def assert_dont_see(self, text: str) -> "TestResponse":
        assert text not in self.content, f"Did not expect to see '{text}' in response"
        return self

    def assert_header(self, name: str, value: str | None = None) -> "TestResponse":
        headers = {k.lower(): v for k, v in self._response._headers.items()}
        ct_lower = self._response.content_type.lower() if hasattr(self._response, "content_type") else ""
        assert name.lower() in headers or name.lower() == "content-type", \
            f"Header '{name}' not found"
        if value is not None:
            actual = headers.get(name.lower(), ct_lower)
            assert value in actual, f"Header '{name}': expected '{value}', got '{actual}'"
        return self


class HuntTestCase:
    """Base test class providing HTTP test helpers."""

    kernel: HttpKernel | None = None

    def _make_scope(self, method: str, path: str, headers: dict | None = None) -> dict:
        raw_headers = []
        for k, v in (headers or {}).items():
            raw_headers.append((k.lower().encode(), v.encode()))
        return {
            "type": "http",
            "method": method.upper(),
            "path": path,
            "query_string": b"",
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

        response = await self.kernel._handle(request)
        return TestResponse(response, response._body)

    async def get(self, path: str, headers: dict | None = None) -> TestResponse:
        return await self._call("GET", path, headers=headers)

    async def post(self, path: str, data: dict | None = None, json: Any = None, headers: dict | None = None) -> TestResponse:
        return await self._call("POST", path, data=data, json_data=json, headers=headers)

    async def put(self, path: str, data: dict | None = None, json: Any = None, headers: dict | None = None) -> TestResponse:
        return await self._call("PUT", path, data=data, json_data=json, headers=headers)

    async def patch(self, path: str, data: dict | None = None, json: Any = None, headers: dict | None = None) -> TestResponse:
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


class RefreshDatabase:
    """Mixin: truncate tables touched during each test."""

    _tables_created: list[str] = []

    def setup_method(self) -> None:
        pass

    def teardown_method(self) -> None:
        from hunt.database.connection import connection
        from sqlalchemy import text
        engine = connection()
        with engine.connect() as conn:
            for table in reversed(self._tables_created):
                try:
                    conn.execute(text(f"DELETE FROM {table}"))
                except Exception:
                    pass
            conn.commit()
