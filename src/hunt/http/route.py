from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

# Matches {name}, {name?}, {name:constraint}, {name:constraint?}
_PARAM_RE = re.compile(r"\{(\w+)(?::([^}?]+))?(\?)?}")


class Route:
    def __init__(
        self,
        methods: list[str],
        uri: str,
        action: Callable,
        name: str | None = None,
        middleware: list[Any] | None = None,
        domain: str | None = None,
        router: Any | None = None,
    ) -> None:
        self._router = router
        self.methods = [m.upper() for m in methods]
        self.uri = uri
        self.action = action
        self._name = name
        self._middleware: list[Any] = middleware or []
        self._max_body: int | None = None
        self._stream_body = False
        self._pattern, self._param_names = self._compile(uri)
        self._tokens = self._tokenise(uri)
        self._domain = domain
        if domain is not None:
            self._domain_pattern, self._domain_param_names = self._compile_domain(domain)
        else:
            self._domain_pattern = None
            self._domain_param_names: list[str] = []
        if name and router is not None:
            router._register_named(self)

    @property
    def name(self) -> str | None:
        return self._name

    def named(self, name: str) -> Route:
        previous = self._name
        self._name = name
        if self._router is not None:
            try:
                self._router._register_named(self, previous=previous)
            except Exception:
                self._name = previous
                raise
        return self

    def middleware(self, *mw: Any) -> Route:
        self._middleware.extend(mw)
        return self

    def max_body(self, n_bytes: int) -> Route:
        """Limit the request body of this route to ``n_bytes``, overriding the
        global ``MAX_BODY_SIZE`` (it may be smaller or larger)."""
        if isinstance(n_bytes, bool) or not isinstance(n_bytes, int) or n_bytes < 0:
            raise ValueError("Route.max_body() takes a non-negative number of bytes.")
        self._max_body = n_bytes
        return self

    def stream_body(self) -> Route:
        """Do not buffer the request body before calling the handler.

        The handler reads it with ``async for chunk in request.stream():``; the
        synchronous accessors (``input``, ``json``, ``file``...) are unavailable
        until ``await request.load_body()``. Only for ``async def`` handlers.
        """
        if not _is_async_callable(self.action):
            raise TypeError(
                f"Route '{self.uri}': stream_body() needs an 'async def' handler, because the body "
                "is read with 'async for chunk in request.stream()'."
            )
        self._stream_body = True
        return self

    def matches(self, method: str, path: str, host: str | None = None) -> tuple[bool, dict[str, str]]:
        if method.upper() not in self.methods:
            return False, {}
        m = self._pattern.fullmatch(path)
        if m is None:
            return False, {}
        params = dict(m.groupdict())

        if self._domain_pattern is not None:
            if not host:
                return False, {}
            dm = self._domain_pattern.fullmatch(host)
            if dm is None:
                return False, {}
            params.update(dm.groupdict())

        return True, params

    def url(self, params: dict[str, Any] | None = None) -> str:
        """Build the path for this route, percent-encoding each parameter.

        Raises ``ValueError`` when a required parameter is missing. A missing
        optional parameter is dropped together with its leading slash.
        """
        params = params or {}
        out: list[str] = []
        for kind, value, constraint, optional in self._tokens:
            if kind == "literal":
                out.append(value)
                continue
            supplied = params.get(value)
            if supplied is None or supplied == "":
                if not optional:
                    raise ValueError(f"Missing required parameter '{value}' for route '{self._name or self.uri}'.")
                if out and out[-1].endswith("/"):
                    out[-1] = out[-1][:-1]
                continue
            # A constraint that can span segments (e.g. {path:.+}) keeps its
            # slashes; everything else is encoded so a value cannot add path
            # segments, a query string or a fragment.
            safe = "/" if constraint and self._spans_segments(constraint) else ""
            out.append(quote(str(supplied), safe=safe))
        return "".join(out) or "/"

    @staticmethod
    def _spans_segments(constraint: str) -> bool:
        try:
            return re.fullmatch(constraint, "a/b") is not None
        except re.error:
            return False

    @staticmethod
    def _tokenise(uri: str) -> list[tuple[str, str, str | None, bool]]:
        """Split a URI into ``(kind, value, constraint, optional)`` tokens."""
        tokens: list[tuple[str, str, str | None, bool]] = []
        pos = 0
        for m in _PARAM_RE.finditer(uri):
            if m.start() > pos:
                tokens.append(("literal", uri[pos : m.start()], None, False))
            tokens.append(("param", m.group(1), m.group(2), bool(m.group(3))))
            pos = m.end()
        if pos < len(uri):
            tokens.append(("literal", uri[pos:], None, False))
        return tokens

    @staticmethod
    def _compile(uri: str) -> tuple[re.Pattern, list[str]]:
        param_names: list[str] = []
        parts: list[str] = []
        tokens = Route._tokenise(uri)
        for idx, (kind, value, constraint, optional) in enumerate(tokens):
            if kind == "literal":
                literal = value
                nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
                # The slash before an optional parameter belongs to the optional
                # group, so /users/{id?} matches both /users and /users/5.
                if nxt is not None and nxt[0] == "param" and nxt[3] and literal.endswith("/"):
                    literal = literal[:-1]
                # Literal text is escaped: "." in /file.json must not match any character.
                parts.append(re.escape(literal))
                continue
            param_names.append(value)
            regex = constraint if constraint else "[^/]+"
            group = f"(?P<{value}>{regex})"
            if optional:
                prev = tokens[idx - 1] if idx > 0 else None
                if prev is not None and prev[0] == "literal" and prev[1].endswith("/"):
                    group = f"(?:/{group})?"
                else:
                    group = f"{group}?"
            parts.append(group)
        return re.compile("".join(parts) or "/"), param_names

    @staticmethod
    def _compile_domain(domain: str) -> tuple[re.Pattern, list[str]]:
        """Compile a domain pattern like '{account}.example.com' to a regex."""
        param_names: list[str] = []

        def replacer(m: re.Match) -> str:
            pname = m.group(1)
            param_names.append(pname)
            return f"(?P<{pname}>[^.]+)"

        pattern = re.sub(r"\{(\w+)\}", replacer, re.escape(domain))
        # re.escape turns {account} into \{account\} before our sub runs,
        # so we need to escape first then replace the escaped placeholders.
        # Instead: escape non-param parts only.
        param_names.clear()
        pattern = _compile_domain_pattern(domain, param_names)
        return re.compile(pattern, re.IGNORECASE), param_names


def _is_async_callable(action: Any) -> bool:
    while isinstance(action, functools.partial):
        action = action.func
    if inspect.iscoroutinefunction(action):
        return True
    call = getattr(action, "__call__", None)  # noqa: B004 - a callable object's own __call__
    return call is not None and inspect.iscoroutinefunction(call)


def _compile_domain_pattern(domain: str, param_names: list[str]) -> str:
    """Build a regex string for a domain pattern, escaping literal parts."""
    parts = re.split(r"(\{[^}]+\})", domain)
    result = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            pname = part[1:-1]
            param_names.append(pname)
            result.append(f"(?P<{pname}>[^.]+)")
        else:
            result.append(re.escape(part))
    return "".join(result)
