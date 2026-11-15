from __future__ import annotations

import importlib.util
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

# Per-request locale override: (translator, request, locale). The translator is a
# singleton shared by all concurrent requests, so a request must not change its locale
# for everybody else.
_locale_override: ContextVar[Any] = ContextVar("hunt_locale_override", default=None)

# Upper bound on cached (locale, group) entries; locales can come from user input.
_MAX_CACHE_ENTRIES = 256

_PLACEHOLDER_RE = re.compile(r":(\w+)")

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

    @staticmethod
    def _current_request() -> Any:
        try:
            from hunt.auth.manager import _get_current_request

            return _get_current_request()
        except Exception:
            return None

    def set_locale(self, locale: str) -> None:
        """Set the locale: for the current request only when one is active, else the default."""
        request = self._current_request()
        if request is None:
            self._locale = locale
        else:
            _locale_override.set((self, request, locale))

    def set_default_locale(self, locale: str) -> None:
        """Set the application-wide default locale, regardless of any active request."""
        self._locale = locale

    def get_locale(self) -> str:
        override = _locale_override.get()
        if override is not None and override[0] is self and override[1] is self._current_request():
            return override[2]
        return self._locale

    def set_fallback(self, locale: str) -> None:
        self._fallback = locale

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has(self, key: str, locale: str | None = None) -> bool:
        return self._resolve(key, locale or self.get_locale()) is not None

    def get(
        self,
        key: str,
        replace: dict[str, Any] | None = None,
        locale: str | None = None,
    ) -> str:
        loc = locale or self.get_locale()
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
        loc = locale or self.get_locale()
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
        while len(self._cache) >= _MAX_CACHE_ENTRIES:
            try:
                del self._cache[next(iter(self._cache))]
            except (StopIteration, KeyError, RuntimeError):
                break

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
        """Replace ``:name`` placeholders in one pass.

        Whole placeholder names are matched (``:name`` does not touch ``:name_full``) and
        substituted values are never scanned again, so a value containing ``:other``
        cannot pull in another replacement.
        """
        if not replace:
            return text
        values: dict[str, str] = {}
        for key, value in replace.items():
            values[str(key)] = str(value)
        for key, value in replace.items():
            values[str(key).capitalize()] = str(value).capitalize()
        for key, value in replace.items():
            values[str(key).upper()] = str(value).upper()
        # An exact key always wins for its own spelling.
        for key, value in replace.items():
            if str(key) not in (str(key).upper(), str(key).capitalize()):
                values[str(key)] = str(value)

        def _sub(match: re.Match) -> str:
            name = match.group(1)
            return values.get(name, match.group(0))

        return _PLACEHOLDER_RE.sub(_sub, text)

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
