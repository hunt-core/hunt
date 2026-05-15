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
