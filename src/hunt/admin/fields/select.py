from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hunt.admin.field import Field


class Select(Field):
    field_type: str = "select"

    def __init__(
        self,
        name: str,
        options: list | Callable[[], list] | None = None,
        attribute: str | None = None,
    ) -> None:
        super().__init__(name, attribute)
        self._options = options if options is not None else []

    def from_form_value(self, value: Any, current: Any = None) -> Any:
        # The blank "— Select —" option means "no value": store NULL when the
        # column allows it (``.nullable()``) or already holds NULL.
        if value == "" and (self._nullable or current is None):
            return None
        return value

    def get_options(self) -> list[dict]:
        raw = self._options() if callable(self._options) else self._options
        normalised = []
        for item in raw:
            if isinstance(item, str):
                normalised.append({"label": item, "value": item})
            elif isinstance(item, dict):
                normalised.append(item)
            else:
                normalised.append({"label": str(item), "value": str(item)})
        return normalised
