from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


class Route:
    def __init__(
        self,
        methods: list[str],
        uri: str,
        action: Callable,
        name: str | None = None,
        middleware: list[Any] | None = None,
        domain: str | None = None,
    ) -> None:
        self.methods = [m.upper() for m in methods]
        self.uri = uri
        self.action = action
        self._name = name
        self._middleware: list[Any] = middleware or []
        self._pattern, self._param_names = self._compile(uri)
        self._domain = domain
        if domain is not None:
            self._domain_pattern, self._domain_param_names = self._compile_domain(domain)
        else:
            self._domain_pattern = None
            self._domain_param_names: list[str] = []

    @property
    def name(self) -> str | None:
        return self._name

    def named(self, name: str) -> Route:
        self._name = name
        return self

    def middleware(self, *mw: Any) -> Route:
        self._middleware.extend(mw)
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
        result = self.uri
        for name in self._param_names:
            value = (params or {}).get(name, f"{{{name}}}")
            result = re.sub(r"\{" + re.escape(name) + r"(?::[^}]+)?\??}", str(value), result)
        return result

    @staticmethod
    def _compile(uri: str) -> tuple[re.Pattern, list[str]]:
        param_names: list[str] = []
        pattern = uri

        def replacer(m: re.Match) -> str:
            pname = m.group(1)
            constraint = m.group(2)  # e.g. r"\d+" from {id:\d+}
            optional = bool(m.group(3))
            param_names.append(pname)
            regex = constraint if constraint else "[^/]+"
            if optional:
                return f"(?P<{pname}>{regex})?"
            return f"(?P<{pname}>{regex})"

        # Matches {name}, {name?}, {name:constraint}, {name:constraint?}
        pattern = re.sub(r"\{(\w+)(?::([^}?]+))?(\?)?}", replacer, pattern)
        return re.compile(pattern), param_names

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
