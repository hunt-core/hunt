from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from email.message import Message
from email.utils import collapse_rfc2231_value
from typing import Any
from urllib.parse import parse_qs, unquote

# Input keys that are never flashed to the session as "old input".
_SENSITIVE_FLASH_KEYS = frozenset(
    {
        "password",
        "password_confirmation",
        "current_password",
        "new_password",
        "new_password_confirmation",
        "_token",
    }
)

# Methods a POST form may ask to be dispatched as, via a ``_method`` field.
_OVERRIDABLE_METHODS = frozenset({"PUT", "PATCH", "DELETE"})


_DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB


def default_max_body_bytes(default: int = _DEFAULT_MAX_BODY_BYTES) -> int:
    """The global request body limit: ``MAX_BODY_SIZE`` (bytes), else 10 MB."""
    raw = os.environ.get("MAX_BODY_SIZE", "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return default


class ClientDisconnected(Exception):
    """The client went away before the request body was complete.

    Raised while the body is being read. The kernel answers it by dropping the
    request: no handler runs on a truncated body and no response is sent.
    """


def _body_too_large() -> Exception:
    from hunt.http.response import HttpException

    # The rest of the body is not going to be read, so the connection cannot be reused.
    return HttpException(
        413, "Request body too large.", headers={"Connection": "close", "X-Content-Type-Options": "nosniff"}
    )


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
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        # OOXML (.xlsx/.docx/.pptx), ODF and plain .zip all share this
        # container. The bytes cannot tell them apart — callers that need the
        # specific format must disambiguate via the filename.
        return "application/zip"
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
        receive: Any | None = None,
    ) -> None:
        self._scope = scope
        self._body = body
        # With an ASGI ``receive`` and no ready body, the body is read lazily:
        # see ``load_body()`` and ``stream()``.
        self._receive = receive if not body else None
        self._body_loaded = self._receive is None
        self._body_limit: int | None = None  # None: the global MAX_BODY_SIZE
        self._received_bytes = 0
        self._receive_done = self._receive is None
        self._stream_started = False
        self._disconnected = False
        self._too_large = False
        self._path_params: dict[str, str] = path_params or {}
        self._parsed_form: dict[str, list[str]] | None = None
        self._parsed_json: Any = None
        self._json_parsed = False
        self._parsed_query: dict[str, list[str]] | None = None
        self._parsed_files: dict[str, UploadedFile] | None = None
        self._parsed_file_lists: dict[str, list[UploadedFile]] = {}
        self._route_params: dict[str, Any] = {}
        self._method_override: str | None = None
        #: The matched ``Route``; set by the kernel before any middleware runs.
        #: None when no route matched (404/405/implicit OPTIONS).
        self.route: Any = None

    # ------------------------------------------------------------------
    # Path / method
    # ------------------------------------------------------------------

    @property
    def method(self) -> str:
        if self._method_override is not None:
            return self._method_override
        return self._scope.get("method", "GET").upper()

    @property
    def real_method(self) -> str:
        """The method on the wire, ignoring any ``_method`` form override."""
        return self._scope.get("method", "GET").upper()

    def apply_method_override(self) -> None:
        """Honour a ``_method`` form field (PUT/PATCH/DELETE) on a POST.

        HTML forms can only submit GET and POST, so a POST whose *form body*
        carries ``_method`` is dispatched as that method. The query string, JSON
        bodies and headers are deliberately ignored, as is every non-POST
        request: a cross-site GET can therefore never be upgraded, and the
        overridden methods are all state-changing, so CSRF still applies.
        """
        if self._method_override is not None or self.real_method != "POST" or self.is_json():
            return
        ct = (self.header("content-type", "") or "").lower()
        if "application/x-www-form-urlencoded" not in ct and "multipart/form-data" not in ct:
            return
        values = self._form().get("_method")
        if not values:
            return
        candidate = str(values[-1]).strip().upper()
        if candidate in _OVERRIDABLE_METHODS:
            self._method_override = candidate

    @property
    def path(self) -> str:
        return self._scope.get("path", "/")

    @property
    def host(self) -> str:
        """Return the request host without port (e.g. 'api.example.com')."""
        return self._split_host(self.header("host", "") or "")[0].lower()

    @staticmethod
    def _split_host(raw: str) -> tuple[str, str]:
        """Split a Host header into (host, port); IPv6 literals keep their brackets."""
        raw = raw.strip()
        if raw.startswith("["):
            end = raw.find("]")
            if end != -1:
                rest = raw[end + 1 :]
                return raw[: end + 1], rest[1:] if rest.startswith(":") else ""
            return raw, ""
        if raw.count(":") == 1:
            host, _, port = raw.partition(":")
            return host, port
        # No port, or a bare (unbracketed) IPv6 address — leave it whole.
        return raw, ""

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
        # Prefer the Host header (what the client asked for); the ASGI ``server``
        # tuple is the bind address of the socket, e.g. 0.0.0.0:8000.
        host = (self.header("host", "") or "").strip()
        if not host:
            server = self._scope.get("server") or ("localhost", None)
            name = str(server[0])
            if ":" in name and not name.startswith("["):
                name = f"[{name}]"
            port = server[1] if len(server) > 1 else None
            default_port = 443 if scheme in ("https", "wss") else 80
            host = name if port in (None, default_port) else f"{name}:{port}"
        return f"{scheme}://{host}{self.path}"

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def input(self, key: str, default: Any = None) -> Any:
        if key in self._path_params:
            return self._path_params[key]
        data = self._all_input()
        return data.get(key, default)

    def _query(self) -> dict[str, list[str]]:
        if self._parsed_query is None:
            raw = self._scope.get("query_string", b"") or b""
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            self._parsed_query = parse_qs(raw, errors="replace")
        return self._parsed_query

    def query(self, key: str, default: Any = None) -> Any:
        """Return the first value of a query string parameter."""
        values = self._query().get(key)
        if not values:
            return default
        return values[0]

    def query_all(self, key: str) -> list[str]:
        """Return all values for a query string parameter (for multi-value params)."""
        return list(self._query().get(key, []))

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
            parsed = dict(self._body_input())
            parsed.update(data)
            self._parsed_json = parsed
            self._json_parsed = True
        else:
            form = dict(self._form())
            for k, v in data.items():
                form[k] = [str(v)]
            self._parsed_form = form

    def _body_input(self) -> dict[str, Any]:
        """Input from the request body only (JSON object or form fields)."""
        if self.is_json():
            data = self._json()
            # A JSON body that is a list, string or number carries no named input.
            return data if isinstance(data, dict) else {}
        return {k: (v[0] if len(v) == 1 else v) for k, v in self._form().items()}

    def _all_input(self) -> dict[str, Any]:
        """Query-string parameters with body input layered on top (body wins)."""
        query = {k: (v[0] if len(v) == 1 else v) for k, v in self._query().items()}
        if not query:
            return self._body_input()
        return {**query, **self._body_input()}

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def is_json(self) -> bool:
        ct = (self.header("content-type", "") or "").lower()
        return "application/json" in ct or "+json" in ct

    def json(self, key: str | None = None, default: Any = None) -> Any:
        data = self._json()
        if key is None:
            return data
        if isinstance(data, dict):
            return data.get(key, default)
        return default

    def _json(self) -> Any:
        if not self._json_parsed:
            self._json_parsed = True
            body = self._loaded_body()
            if body:
                try:
                    self._parsed_json = json.loads(body)
                except (ValueError, RecursionError):
                    # Malformed, non-UTF-8 or absurdly nested JSON is "no input".
                    self._parsed_json = {}
        return self._parsed_json or {}

    # ------------------------------------------------------------------
    # Form / multipart
    # ------------------------------------------------------------------

    def _form(self) -> dict[str, list[str]]:
        if self._parsed_form is None:
            body = self._loaded_body()
            ct = self.header("content-type", "") or ""
            if "multipart/form-data" in ct.lower():
                fields, files = self._parse_multipart(ct)
                self._parsed_form = fields
                self._parsed_file_lists = files
                self._parsed_files = {name: items[-1] for name, items in files.items() if items}
            else:
                self._parsed_form = parse_qs(
                    body.decode("utf-8", errors="replace"), keep_blank_values=True, errors="replace"
                )
        return self._parsed_form

    def _parse_multipart(self, content_type: str) -> tuple[dict[str, list[str]], dict[str, list[UploadedFile]]]:
        """Parse a multipart/form-data body into (fields, files).

        Repeated names are collected into lists. Part headers are parsed with
        :mod:`email.message`, so a quoted ``;`` inside a parameter cannot smuggle
        a second ``name=``.
        """
        boundary = _header_param(content_type, "boundary")
        if not boundary:
            return {}, {}

        fields: dict[str, list[str]] = {}
        files: dict[str, list[UploadedFile]] = {}

        # A delimiter is CRLF + "--" + boundary, then either "--" (the closing
        # delimiter) or optional padding and CRLF. Anchoring on the line break
        # means boundary text inside a part body is not treated as a delimiter.
        delimiter = re.compile(rb"\r\n--" + re.escape(boundary.encode("utf-8", "replace")) + rb"(--)?[ \t]*(?:\r\n|$)")
        pieces = delimiter.split(b"\r\n" + self._loaded_body())
        # pieces = [preamble, closing-flag, part, closing-flag, part, ...]
        for idx in range(1, len(pieces) - 1, 2):
            if pieces[idx]:  # closing delimiter: everything after it is epilogue
                break
            part = pieces[idx + 1]
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            body_data = part[header_end + 4 :]

            headers: dict[str, str] = {}
            for line in part[:header_end].split(b"\r\n"):
                if b":" in line:
                    k, _, v = line.partition(b":")
                    headers[k.strip().lower().decode("utf-8", errors="replace")] = v.strip().decode(
                        "utf-8", errors="replace"
                    )

            cd = headers.get("content-disposition", "")
            name = _header_param(cd, "name")
            if not name:
                continue
            filename = _header_param(cd, "filename")

            if filename is not None:
                ct = headers.get("content-type", "application/octet-stream")
                upload = UploadedFile(filename=_safe_filename(filename), content_type=ct, _data=body_data)
                files.setdefault(name, []).append(upload)
            else:
                fields.setdefault(name, []).append(body_data.decode("utf-8", errors="replace"))

        return fields, files

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------

    def file(self, name: str) -> UploadedFile | None:
        """Return the uploaded file for *name* (the last one if it was repeated)."""
        self._form()  # triggers multipart parsing
        return (self._parsed_files or {}).get(name)

    def files(self, name: str) -> list[UploadedFile]:
        """Return every uploaded file for *name* (e.g. ``<input type=file multiple>``)."""
        self._form()
        return list(self._parsed_file_lists.get(name, []))

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
                value = v.strip()
                if len(value) >= 2 and value[0] == value[-1] == '"':
                    value = value[1:-1]
                # Response.with_cookie() percent-encodes the value; undo that.
                return unquote(value, errors="replace") or default
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
        return {
            k.decode("latin-1").lower(): v.decode("utf-8", errors="replace") for k, v in self._scope.get("headers", [])
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
        """Return client IP from the ASGI transport layer.

        Never read X-Forwarded-For here — it is trivially spoofed by clients.
        """
        client = self._scope.get("client")
        if client and client[0]:
            return str(client[0])
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
        accept = (self.header("accept", "") or "").lower()
        return "application/json" in accept or "+json" in accept

    @property
    def body(self) -> bytes:
        return self._loaded_body()

    # ------------------------------------------------------------------
    # Lazy body
    # ------------------------------------------------------------------

    @property
    def body_loaded(self) -> bool:
        """True once the body is in memory and the synchronous accessors work."""
        return self._body_loaded

    def _loaded_body(self) -> bytes:
        if not self._body_loaded:
            raise RuntimeError(
                "The request body has not been read yet. Call 'await request.load_body()' first. "
                "This happens in a middleware that sets 'reads_body = False' (remove that attribute "
                "if the middleware uses request input) or in the handler of a route registered with "
                "'.stream_body()' (read the body with 'async for chunk in request.stream()')."
            )
        return self._body

    def _max_body_bytes(self) -> int:
        return self._body_limit if self._body_limit is not None else default_max_body_bytes()

    def _declared_length(self) -> int | None:
        for key, value in self._scope.get("headers", []):
            if key.lower() == b"content-length":
                declared = value.strip()
                return int(declared) if declared.isdigit() else None
        return None

    def _exceeds_body_limit(self) -> bool:
        """True when the body is known to be over the limit without reading (more of) it."""
        if self._too_large:
            return True
        if self._receive is None:
            return False  # a body handed to the constructor is not limited here
        if self._body_loaded and not self._stream_started:
            return len(self._body) > self._max_body_bytes()
        if self._received_bytes == 0 and not self._receive_done:
            declared = self._declared_length()
            return declared is not None and declared > self._max_body_bytes()
        return False

    async def _receive_chunks(self) -> AsyncIterator[bytes]:
        """Yield body chunks from the ASGI server, enforcing the size limit.

        ``receive()`` is never called again once the server reported the end of
        the body (``more_body`` false), a disconnect, or the limit was exceeded.
        """
        if self._disconnected:
            raise ClientDisconnected()
        if self._exceeds_body_limit():
            self._too_large = True
            self._receive_done = True
            raise _body_too_large()
        limit = self._max_body_bytes()
        receive = self._receive
        while not self._receive_done and receive is not None:
            message = await receive()
            if message.get("type") == "http.disconnect":
                self._receive_done = True
                self._disconnected = True
                raise ClientDisconnected()
            if not message.get("more_body", False):
                self._receive_done = True
            chunk = message.get("body", b"")
            if chunk:
                self._received_bytes += len(chunk)
                if self._received_bytes > limit:
                    self._receive_done = True
                    self._too_large = True
                    raise _body_too_large()
                yield chunk

    async def load_body(self) -> bytes:
        """Read the whole body into memory (idempotent) and return it.

        Raises ``HttpException(413)`` when it exceeds the limit (``MAX_BODY_SIZE``
        or the route's ``max_body()``); a declared ``Content-Length`` over the
        limit is refused before anything is read. Raises ``ClientDisconnected``
        when the client goes away mid-body.

        After ``stream()`` has consumed the body there is nothing left: the body
        is then empty (after a partial ``stream()``, the unread remainder).
        """
        if self._body_loaded:
            return self._body
        chunks: list[bytes] = []
        async for chunk in self._receive_chunks():
            chunks.append(chunk)
        self._body = b"".join(chunks)
        self._body_loaded = True
        return self._body

    async def stream(self) -> AsyncIterator[bytes]:
        """Iterate over the body in chunks as they arrive, without buffering it.

        For handlers of routes registered with ``.stream_body()``. Usable once.
        The size limit is enforced while reading (``HttpException(413)``). When
        the body was already loaded it is yielded as a single chunk.
        """
        if self._stream_started:
            raise RuntimeError("request.stream() can only be used once per request.")
        self._stream_started = True
        if self._body_loaded:
            if self._body:
                yield self._body
            return
        async for chunk in self._receive_chunks():
            yield chunk
        # Fully consumed: nothing is buffered, and load_body()/body must not wait for more.
        self._body = b""
        self._body_loaded = True

    def _abandon_body(self) -> None:
        """Give up on a body nobody read (an error response is being produced).

        The synchronous accessors then see an empty body instead of raising, so
        exception handlers and error hooks can still inspect the request.
        """
        if not self._body_loaded:
            self._body = b""
            self._body_loaded = True
            self._receive_done = True

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
            store.flash("_old_input", self._flashable(self.all()))

    def flash_only(self, *keys: str) -> None:
        store = getattr(self, "_session", None)
        if store is not None:
            store.flash("_old_input", self._flashable(self.only(*keys)))

    def flash_except(self, *keys: str) -> None:
        store = getattr(self, "_session", None)
        if store is not None:
            store.flash("_old_input", self._flashable(self.except_(*keys)))

    @staticmethod
    def _flashable(data: dict[str, Any]) -> dict[str, Any]:
        """Drop secrets (passwords, the CSRF token) and uploads before flashing:
        old input is written to the session store and echoed back into forms."""
        return {k: v for k, v in data.items() if k not in _SENSITIVE_FLASH_KEYS and not isinstance(v, UploadedFile)}


def _header_param(header: str, param: str) -> str | None:
    """Return one parameter of a ``value; key="quoted; value"`` style header.

    Parameter names are case-insensitive and quoted strings are honoured.
    """
    if not header:
        return None
    msg = Message()
    try:
        msg["x-hunt"] = header
        value = msg.get_param(param, header="x-hunt")
    except Exception:
        return None
    if value is None:
        return None
    try:
        return collapse_rfc2231_value(value)
    except Exception:
        return None


def _safe_filename(filename: str) -> str:
    """Reduce a client-supplied filename to a single safe path component."""
    cleaned = "".join(ch for ch in filename if ch >= " " and ch != "\x7f")
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if cleaned in ("", ".", ".."):
        return "upload"
    return cleaned
