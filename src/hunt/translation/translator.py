from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

_LOCALE_RE = re.compile(r"^[a-zA-Z]{2,8}([_-][a-zA-Z0-9]{2,8})*$")
_GROUP_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_locale(locale: str) -> None:
    if not _LOCALE_RE.match(locale):
        raise ValueError(f"Invalid locale identifier: {locale!r}")


def _validate_group(group: str) -> None:
    if not _GROUP_RE.match(group):
        raise ValueError(f"Invalid translation group identifier: {group!r}")


class Translator:
    """Resolve translation keys from lang/{locale}/{group}.py dict files.

    Key format: ``"group.key"`` or ``"group.nested.key"``.
    Lang files export a module-level dict with the same name as the file::

        # lang/en/messages.py
        messages = {
            "welcome": "Welcome!",
            "greeting": "Hello, :name!",
            "apples": "{0} no apples|{1} one apple|[2,*] :count apples",
        }
    """

    def __init__(
        self,
        lang_path: str | Path,
        locale: str = "en",
        fallback_locale: str = "en",
    ) -> None:
        self._lang_path = Path(lang_path)
        self._locale = locale
        self._fallback = fallback_locale
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Locale management
    # ------------------------------------------------------------------

    def set_locale(self, locale: str) -> None:
        self._locale = locale

    def get_locale(self) -> str:
        return self._locale

    def set_fallback(self, locale: str) -> None:
        self._fallback = locale

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has(self, key: str, locale: str | None = None) -> bool:
        return self._resolve(key, locale or self._locale) is not None

    def get(
        self,
        key: str,
        replace: dict[str, Any] | None = None,
        locale: str | None = None,
    ) -> str:
        loc = locale or self._locale
        value = self._resolve(key, loc)
        if value is None and loc != self._fallback:
            value = self._resolve(key, self._fallback)
        if value is None:
            return key
        return self._substitute(str(value), replace or {})

    def choice(
        self,
        key: str,
        count: int,
        replace: dict[str, Any] | None = None,
        locale: str | None = None,
    ) -> str:
        loc = locale or self._locale
        value = self._resolve(key, loc)
        if value is None and loc != self._fallback:
            value = self._resolve(key, self._fallback)
        if value is None:
            return key
        segment = self._select_plural(str(value), count)
        merged: dict[str, Any] = dict(replace or {})
        merged.setdefault("count", count)
        return self._substitute(segment, merged)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve(self, key: str, locale: str) -> str | None:
        parts = key.split(".", 1)
        if len(parts) < 2:
            return None
        group, item = parts
        try:
            _validate_locale(locale)
            _validate_group(group)
        except ValueError:
            return None
        data = self._load_group(locale, group)
        # Support nested keys: "auth.failed" → data["auth"]["failed"]
        value: Any = data
        for k in item.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(k)
        return value if isinstance(value, str) else None

    def _load_group(self, locale: str, group: str) -> dict[str, Any]:
        cache_key = (locale, group)
        if cache_key in self._cache:
            return self._cache[cache_key]

        lang_root = self._lang_path.resolve()
        path = (self._lang_path / locale / f"{group}.py").resolve()
        if not str(path).startswith(str(lang_root) + "/"):
            raise ValueError(f"Translation path escapes lang directory: {path}")
        if not path.exists():
            self._cache[cache_key] = {}
            return {}

        spec = importlib.util.spec_from_file_location(f"_lang_{locale}_{group}", path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        # Prefer a dict attribute matching the group name; fall back to first dict found.
        data = getattr(mod, group, None)
        if not isinstance(data, dict):
            for name, attr in vars(mod).items():
                if not name.startswith("_") and isinstance(attr, dict):
                    data = attr
                    break

        result: dict[str, Any] = data if isinstance(data, dict) else {}
        self._cache[cache_key] = result
        return result

    def _substitute(self, text: str, replace: dict[str, Any]) -> str:
        for key, value in replace.items():
            text = text.replace(f":{key.upper()}", str(value).upper())
            text = text.replace(f":{key.capitalize()}", str(value).capitalize())
            text = text.replace(f":{key}", str(value))
        return text

    def _select_plural(self, value: str, count: int) -> str:
        """Pick the correct plural form from a pipe-delimited string.

        Laravel-style qualifiers::

            "{0} No items|{1} One item|[2,*] :count items"
            "[1,1] one apple|[2,*] :count apples"

        Simple ngettext-style (no qualifiers)::

            "one apple|many apples"  → count==1 → first, else last
        """
        segments = [s.strip() for s in value.split("|")]
        if len(segments) == 1:
            return segments[0]

        _qualifier = re.compile(r"^(\{[\d,*\s]+\}|\[\d+,[\d*]+\])\s*")
        has_qualifiers = any(_qualifier.match(s) for s in segments)

        if has_qualifiers:
            for segment in segments:
                m = _qualifier.match(segment)
                if not m:
                    continue
                if self._qualifier_matches(m.group(1), count):
                    return segment[m.end() :]
            # No explicit match — strip qualifier from last segment
            last = segments[-1]
            m = _qualifier.match(last)
            return last[m.end() :] if m else last

        # Simple ngettext: 1 → first segment, otherwise last
        return segments[0] if count == 1 else segments[-1]

    def _qualifier_matches(self, qualifier: str, count: int) -> bool:
        if qualifier.startswith("{"):
            inner = qualifier[1:-1]
            try:
                values = {int(v.strip()) for v in inner.split(",") if v.strip().lstrip("-").isdigit()}
                return count in values
            except ValueError:
                return False
        if qualifier.startswith("["):
            inner = qualifier[1:-1]
            lo_str, hi_str = inner.split(",", 1)
            try:
                lo = int(lo_str.strip())
                hi_s = hi_str.strip()
                hi = None if hi_s == "*" else int(hi_s)
                return count >= lo if hi is None else lo <= count <= hi
            except ValueError:
                return False
        return False
