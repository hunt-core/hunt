from __future__ import annotations

from typing import Any

from hunt.admin.field import Field


class Boolean(Field):
    field_type: str = "boolean"

    def display_value(self, instance: Any) -> str:
        val = self.value_for(instance)
        if val is None:
            return ""
        return "Yes" if val else "No"

    def from_form_value(self, value: Any, current: Any = None) -> Any:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "yes")
        return bool(value)
