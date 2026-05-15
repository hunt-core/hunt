from __future__ import annotations

from typing import Any

from hunt.admin.field import Field


class Number(Field):
    field_type: str = "number"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._min: int | None = None
        self._max: int | None = None

    def min(self, n: int) -> Number:
        self._min = n
        return self

    def max(self, n: int) -> Number:
        self._max = n
        return self


class Currency(Field):
    field_type: str = "currency"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._currency: str = "USD"

    def currency(self, code: str) -> Currency:
        self._currency = code
        return self

    def display_value(self, instance: Any) -> str:
        val = self.value_for(instance)
        if val is None:
            return ""
        try:
            return f"${float(val):.2f}"
        except (ValueError, TypeError):
            return str(val)
