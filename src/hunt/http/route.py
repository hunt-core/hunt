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
    ) -> None:
        self.methods = [m.upper() for m in methods]
        self.uri = uri
        self.action = action
        self._name = name
        self._middleware: list[Any] = middleware or []
        self._pattern, self._param_names = self._compile(uri)

    @property
    def name(self) -> str | None:
        return self._name

    def named(self, name: str) -> Route:
        self._name = name
        return self

    def middleware(self, *mw: Any) -> Route:
        self._middleware.extend(mw)
        return self

    def matches(self, method: str, path: str) -> tuple[bool, dict[str, str]]:
        if method.upper() not in self.methods:
            return False, {}
        m = self._pattern.fullmatch(path)
        if m is None:
            return False, {}
        return True, m.groupdict()

    def url(self, params: dict[str, Any] | None = None) -> str:
        result = self.uri
        for name in self._param_names:
            value = (params or {}).get(name, f"{{{name}}}")
            result = re.sub(r"\{" + name + r"\??}", str(value), result)
        return result

    @staticmethod
    def _compile(uri: str) -> tuple[re.Pattern, list[str]]:
        param_names: list[str] = []
        pattern = uri

        def replacer(m: re.Match) -> str:
            pname = m.group(1)
            optional = m.group(0).endswith("?}")
            param_names.append(pname)
            if optional:
                return f"(?P<{pname}>[^/]*)?"
            return f"(?P<{pname}>[^/]+)"

        pattern = re.sub(r"\{(\w+)\??}", replacer, pattern)
        return re.compile(pattern), param_names
