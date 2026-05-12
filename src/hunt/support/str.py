from __future__ import annotations

import re


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
        return "".join(p.title() for p in parts)

    @staticmethod
    def slug(value: str, separator: str = "-") -> str:
        value = value.lower()
        value = re.sub(r"[^\w\s-]", "", value)
        value = re.sub(r"[\s_-]+", separator, value)
        return value.strip(separator)

    @staticmethod
    def plural(value: str) -> str:
        irregular = {
            "person": "people", "child": "children", "man": "men",
            "woman": "women", "tooth": "teeth", "foot": "feet",
            "mouse": "mice", "goose": "geese",
        }
        lower = value.lower()
        if lower in irregular:
            return irregular[lower]
        if lower.endswith(("s", "x", "z", "ch", "sh")):
            return value + "es"
        if lower.endswith("y") and not lower[-2] in "aeiou":
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
