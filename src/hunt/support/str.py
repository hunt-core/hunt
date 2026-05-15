from __future__ import annotations

import re
import secrets
import string
import uuid as _uuid_mod


class Str:
    @staticmethod
    def snake(value: str) -> str:
        value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
        value = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", value)
        return value.replace("-", "_").lower()

    @staticmethod
    def camel(value: str) -> str:
        parts = re.split(r"[_\-\s]+", value)
        return parts[0].lower() + "".join(p.title() for p in parts[1:])

    @staticmethod
    def pascal(value: str) -> str:
        parts = re.split(r"[_\-\s]+", value)
        # Uppercase only the first character of each part; preserve internal casing
        # so that already-correct PascalCase input (BlogPost) is not destroyed.
        return "".join((p[0].upper() + p[1:]) if p else "" for p in parts)

    @staticmethod
    def slug(value: str, separator: str = "-") -> str:
        value = value.lower()
        value = re.sub(r"[^\w\s-]", "", value)
        value = re.sub(r"[\s_-]+", separator, value)
        return value.strip(separator)

    @staticmethod
    def plural(value: str) -> str:
        irregular = {
            "person": "people",
            "child": "children",
            "man": "men",
            "woman": "women",
            "tooth": "teeth",
            "foot": "feet",
            "mouse": "mice",
            "goose": "geese",
        }
        lower = value.lower()
        if lower in irregular:
            return irregular[lower]
        if lower.endswith(("s", "x", "z", "ch", "sh")):
            return value + "es"
        if lower.endswith("y") and lower[-2] not in "aeiou":
            return value[:-1] + "ies"
        return value + "s"

    @staticmethod
    def singular(value: str) -> str:
        if value.endswith("ies"):
            return value[:-3] + "y"
        if value.endswith("es"):
            return value[:-2]
        if value.endswith("s") and not value.endswith("ss"):
            return value[:-1]
        return value

    @staticmethod
    def title(value: str) -> str:
        return value.replace("_", " ").replace("-", " ").title()

    @staticmethod
    def headline(value: str) -> str:
        """Convert snake_case, camelCase, or words to Title Case headline."""
        value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
        value = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", value)
        value = re.sub(r"[_\-]+", " ", value)
        return " ".join(word.capitalize() for word in value.split())

    @staticmethod
    def limit(value: str, limit: int = 100, end: str = "...") -> str:
        if len(value) <= limit:
            return value
        return value[:limit].rstrip() + end

    @staticmethod
    def words(value: str, words: int = 100, end: str = "...") -> str:
        word_list = value.split()
        if len(word_list) <= words:
            return value
        return " ".join(word_list[:words]) + end

    @staticmethod
    def contains(haystack: str, needles: str | list[str]) -> bool:
        if isinstance(needles, str):
            needles = [needles]
        return any(n in haystack for n in needles)

    @staticmethod
    def starts_with(value: str, prefix: str | list[str]) -> bool:
        if isinstance(prefix, str):
            prefix = [prefix]
        return any(value.startswith(p) for p in prefix)

    @staticmethod
    def ends_with(value: str, suffix: str | list[str]) -> bool:
        if isinstance(suffix, str):
            suffix = [suffix]
        return any(value.endswith(s) for s in suffix)

    @staticmethod
    def after(subject: str, search: str) -> str:
        """Return everything after the first occurrence of search."""
        if not search or search not in subject:
            return subject
        return subject[subject.index(search) + len(search) :]

    @staticmethod
    def after_last(subject: str, search: str) -> str:
        """Return everything after the last occurrence of search."""
        if not search or search not in subject:
            return subject
        return subject[subject.rindex(search) + len(search) :]

    @staticmethod
    def before(subject: str, search: str) -> str:
        """Return everything before the first occurrence of search."""
        if not search or search not in subject:
            return subject
        return subject[: subject.index(search)]

    @staticmethod
    def before_last(subject: str, search: str) -> str:
        """Return everything before the last occurrence of search."""
        if not search or search not in subject:
            return subject
        return subject[: subject.rindex(search)]

    @staticmethod
    def between(subject: str, from_: str, to: str) -> str:
        """Return the substring between from_ and to (first occurrence of each)."""
        after = Str.after(subject, from_)
        return Str.before(after, to)

    @staticmethod
    def squish(value: str) -> str:
        """Collapse all whitespace runs to single spaces and strip ends."""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def wrap(value: str, before: str, after: str | None = None) -> str:
        return f"{before}{value}{after if after is not None else before}"

    @staticmethod
    def is_(pattern: str, value: str) -> bool:
        """Return True if value matches the glob-style pattern (* = any chars)."""
        regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
        return bool(re.fullmatch(regex, value))

    @staticmethod
    def replace_first(search: str, replace: str, subject: str) -> str:
        return subject.replace(search, replace, 1)

    @staticmethod
    def replace_last(search: str, replace: str, subject: str) -> str:
        idx = subject.rfind(search)
        if idx == -1:
            return subject
        return subject[:idx] + replace + subject[idx + len(search) :]

    @staticmethod
    def uuid() -> str:
        return str(_uuid_mod.uuid4())

    @staticmethod
    def random(length: int = 16) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))
