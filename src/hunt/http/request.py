from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse


class Request:
    def __init__(
        self,
        scope: dict,
        body: bytes = b"",
        path_params: dict | None = None,
    ) -> None:
        self._scope = scope
        self._body = body
        self._path_params: dict[str, str] = path_params or {}
        self._parsed_form: dict[str, list[str]] | None = None
        self._parsed_json: Any = None
        self._route_params: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Path / method
    # ------------------------------------------------------------------

    @property
    def method(self) -> str:
        return self._scope.get("method", "GET").upper()

    @property
    def path(self) -> str:
        return self._scope.get("path", "/")

    @property
    def full_url(self) -> str:
        scheme = self._scope.get("scheme", "http")
        server = self._scope.get("server", ("localhost", 80))
        host = f"{server[0]}:{server[1]}"
        return f"{scheme}://{host}{self.path}"

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def input(self, key: str, default: Any = None) -> Any:
        """Get from POST body (form or JSON) or path params."""
        if key in self._path_params:
            return self._path_params[key]
        data = self._all_input()
        return data.get(key, default)

    def query(self, key: str, default: Any = None) -> Any:
        qs = parse_qs(self._scope.get("query_string", b"").decode())
        values = qs.get(key)
        if values is None:
            return default
        return values[0] if len(values) == 1 else values

    def all(self) -> dict[str, Any]:
        return {**self._all_input(), **self._path_params}

    def only(self, *keys: str) -> dict[str, Any]:
        data = self.all()
        return {k: data[k] for k in keys if k in data}

    def except_(self, *keys: str) -> dict[str, Any]:
        data = self.all()
        return {k: v for k, v in data.items() if k not in keys}

    def has(self, key: str) -> bool:
        return key in self.all()

    def filled(self, key: str) -> bool:
        val = self.input(key)
        return val is not None and val != ""

    def _all_input(self) -> dict[str, Any]:
        if self.is_json():
            return self._json() or {}
        return {k: (v[0] if len(v) == 1 else v) for k, v in self._form().items()}

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def is_json(self) -> bool:
        ct = self.header("content-type", "")
        return "application/json" in ct

    def json(self, key: str | None = None, default: Any = None) -> Any:
        data = self._json()
        if key is None:
            return data
        if isinstance(data, dict):
            return data.get(key, default)
        return default

    def _json(self) -> Any:
        if self._parsed_json is None and self._body:
            try:
                self._parsed_json = json.loads(self._body)
            except (json.JSONDecodeError, ValueError):
                self._parsed_json = {}
        return self._parsed_json or {}

    # ------------------------------------------------------------------
    # Form
    # ------------------------------------------------------------------

    def _form(self) -> dict[str, list[str]]:
        if self._parsed_form is None:
            self._parsed_form = parse_qs(self._body.decode("utf-8", errors="replace"))
        return self._parsed_form

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------

    def header(self, name: str, default: str | None = None) -> str | None:
        name_lower = name.lower().encode()
        for k, v in self._scope.get("headers", []):
            if k.lower() == name_lower:
                return v.decode("utf-8", errors="replace")
        return default

    def headers(self) -> dict[str, str]:
        return {
            k.decode(): v.decode()
            for k, v in self._scope.get("headers", [])
        }

    def bearer_token(self) -> str | None:
        auth = self.header("authorization", "")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:]
        return None

    # ------------------------------------------------------------------
    # IP / server
    # ------------------------------------------------------------------

    @property
    def ip(self) -> str:
        """Return the client IP from the ASGI transport layer.

        Never read X-Forwarded-For here — it is trivially spoofed by clients.
        If your app runs behind a trusted reverse proxy, configure the proxy to
        overwrite the ASGI `client` address rather than relying on this header.
        """
        client = self._scope.get("client")
        if client:
            return client[0]
        return "127.0.0.1"

    @property
    def ip_from_proxy(self) -> str:
        """Return the leftmost IP from X-Forwarded-For.

        Only call this when you have verified that the request arrived through
        a trusted reverse proxy (e.g., nginx/Cloudflare that strips and rewrites
        this header). Using it blindly allows clients to spoof their IP.
        """
        xff = self.header("x-forwarded-for", "") or ""
        return xff.split(",")[0].strip() or self.ip

    # ------------------------------------------------------------------
    # Route params (set by router)
    # ------------------------------------------------------------------

    def set_route_params(self, params: dict[str, str]) -> None:
        self._path_params = params

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def is_method(self, *methods: str) -> bool:
        return self.method in {m.upper() for m in methods}

    def expects_json(self) -> bool:
        accept = self.header("accept", "")
        return "application/json" in (accept or "")

    @property
    def body(self) -> bytes:
        return self._body

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def session(self, key: str | None = None, default: Any = None) -> Any:
        store = getattr(self, "_session", None)
        if store is None:
            raise RuntimeError("Session middleware is not active.")
        if key is None:
            return store
        return store.get(key, default)
