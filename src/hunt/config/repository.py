from __future__ import annotations

from typing import Any


class ConfigRepository:
    def __init__(self, items: dict[str, Any]) -> None:
        self._items = items

    def get(self, key: str, default: Any = None) -> Any:
        """Dot-notation access: config('database.default')."""
        parts = key.split(".")
        value: Any = self._items
        for part in parts:
            if not isinstance(value, dict):
                return default
            value = value.get(part, default)
            if value is default:
                return default
        return value

    def set(self, key: str, value: Any) -> None:
        parts = key.split(".")
        target = self._items
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    def all(self) -> dict[str, Any]:
        return self._items

    def __call__(self, key: str, default: Any = None) -> Any:
        return self.get(key, default)
