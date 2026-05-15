from __future__ import annotations

from collections.abc import Callable

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
