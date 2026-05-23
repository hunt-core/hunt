from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs


def _sniff_mime(data: bytes) -> str:
    """Detect MIME type from magic bytes — never trust the client-supplied value."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    stripped = data[:512].lstrip()
    if stripped[:4] == b"<svg" or stripped[:5] == b"<?xml":
        return "image/svg+xml"
    return "application/octet-stream"


@dataclass
class UploadedFile:
    filename: str
    content_type: str
    _data: bytes = field(repr=False)

    @property
    def size(self) -> int:
        return len(self._data)

    @property
    def content(self) -> bytes:
        return self._data

    def store(self, path: str, disk: str = "local") -> str:
        """Save the upload to storage and return the stored path."""
        from hunt.storage.manager import Storage

        return Storage.disk(disk).put_file(path, self)

    def get_client_original_name(self) -> str:
        return self.filename

    def get_mime_type(self) -> str:
        """Return MIME type detected from file content (not the client-supplied header)."""
        return _sniff_mime(self._data)


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
        self._parsed_files: dict[str, UploadedFile] | None = None
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
    def host(self) -> str:
        """Return the request host without port (e.g. 'api.example.com')."""
        raw = self.header("host", "") or ""
        # Strip port if present
        if ":" in raw:
            raw = raw.rsplit(":", 1)[0]
        return raw.lower()

    def subdomain(self, root_domain: str) -> str:
        """Return the subdomain relative to root_domain.

        host='api.example.com', root_domain='example.com' → 'api'
        Returns '' when the host equals root_domain or is not a suffix of it.
        """
        h = self.host
        root = root_domain.lower().lstrip(".")
        if h == root:
            return ""
        suffix = "." + root
        if h.endswith(suffix):
            return h[: -len(suffix)]
        return ""

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

    def missing(self, key: str) -> bool:
        return not self.has(key)

    def filled(self, key: str) -> bool:
        val = self.input(key)
        return val is not None and val != ""

    def merge(self, data: dict[str, Any]) -> None:
        """Merge additional values into the input for this request."""
        if self.is_json():
            parsed = dict(self._json() or {})
            parsed.update(data)
            self._parsed_json = parsed
        else:
            form = dict(self._form())
            for k, v in data.items():
                form[k] = [str(v)]
            self._parsed_form = form

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
    # Form / multipart
    # ------------------------------------------------------------------

    def _form(self) -> dict[str, list[str]]:
        if self._parsed_form is None:
            ct = self.header("content-type", "") or ""
            if "multipart/form-data" in ct:
                fields, files = self._parse_multipart(ct)
                self._parsed_form = {k: [v] for k, v in fields.items()}
                self._parsed_files = files
            else:
                self._parsed_form = parse_qs(self._body.decode("utf-8", errors="replace"))
        return self._parsed_form

    def _parse_multipart(self, content_type: str) -> tuple[dict[str, str], dict[str, UploadedFile]]:
        """Parse multipart/form-data body into (fields, files)."""
        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[9:].strip('"')
                break
        if not boundary:
            return {}, {}

        delimiter = b"--" + boundary.encode()
        fields: dict[str, str] = {}
        files: dict[str, UploadedFile] = {}

        parts = self._body.split(delimiter)
        for part in parts[1:]:
            if part in (b"--\r\n", b"--", b"--\r\n--", b"--\n"):
                break
            if part.startswith(b"\r\n"):
                part = part[2:]
            if part.endswith(b"\r\n"):
                part = part[:-2]

            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_raw = part[:header_end]
            body_data = part[header_end + 4 :]

            headers: dict[str, str] = {}
            for line in headers_raw.split(b"\r\n"):
                if b":" in line:
                    k, _, v = line.partition(b":")
                    headers[k.lower().strip().decode()] = v.strip().decode()

            cd = headers.get("content-disposition", "")
            name: str | None = None
            filename: str | None = None
            for item in cd.split(";"):
                item = item.strip()
                if item.startswith("name="):
                    name = item[5:].strip("\"'")
                elif item.startswith("filename="):
                    filename = item[9:].strip("\"'")

            if not name:
                continue

            if filename is not None:
                ct = headers.get("content-type", "application/octet-stream")
                # Strip null bytes, CRLF, and path components so callers that use
                # the filename directly cannot escape a storage root via ../
                safe_filename = filename.replace("\x00", "").replace("\r", "").replace("\n", "")
                safe_filename = os.path.basename(safe_filename) or "upload"
                files[name] = UploadedFile(filename=safe_filename, content_type=ct, _data=body_data)
            else:
                fields[name] = body_data.decode("utf-8", errors="replace")

        return fields, files

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------

    def file(self, name: str) -> UploadedFile | None:
        self._form()  # triggers multipart parsing
        return (self._parsed_files or {}).get(name)

    def has_file(self, name: str) -> bool:
        return self.file(name) is not None

    # ------------------------------------------------------------------
    # Cookies
    # ------------------------------------------------------------------

    def cookie(self, name: str, default: str | None = None) -> str | None:
        header = self.header("cookie", "") or ""
        for part in header.split(";"):
            k, _, v = part.strip().partition("=")
            if k.strip() == name:
                return v.strip() or default
        return default

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
        return {k.decode(): v.decode() for k, v in self._scope.get("headers", [])}

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
        """Return client IP from the ASGI transport layer.

        Never read X-Forwarded-For here — it is trivially spoofed by clients.
        """
        client = self._scope.get("client")
        if client:
            return client[0]
        return "127.0.0.1"

    @property
    def ip_from_proxy(self) -> str:
        """Return leftmost IP from X-Forwarded-For.

        Only call when you have verified the request arrived through a trusted
        reverse proxy that strips and rewrites this header.
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

    def old(self, field: str, default: Any = None) -> Any:
        """Get old (flashed) input from the previous request."""
        store = getattr(self, "_session", None)
        if store is None:
            return default
        getter = getattr(store, "get_flash", None)
        old_input = getter("_old_input") if getter else store.get("_old_input")
        if not isinstance(old_input, dict):
            return default
        return old_input.get(field, default)

    def flash(self) -> None:
        """Flash the current input to the session for the next request."""
        store = getattr(self, "_session", None)
        if store is not None:
            store.flash("_old_input", self.all())

    def flash_only(self, *keys: str) -> None:
        store = getattr(self, "_session", None)
        if store is not None:
            store.flash("_old_input", self.only(*keys))

    def flash_except(self, *keys: str) -> None:
        store = getattr(self, "_session", None)
        if store is not None:
            store.flash("_old_input", self.except_(*keys))
