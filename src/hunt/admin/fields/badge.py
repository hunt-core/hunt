from __future__ import annotations

from typing import Any

from hunt.admin.field import Field

_DEFAULT_COLOUR = "gray"


class Badge(Field):
    field_type: str = "badge"

    def __init__(
        self,
        name: str,
        colour_map: dict | None = None,
        attribute: str | None = None,
    ) -> None:
        super().__init__(name, attribute)
        self._colour_map: dict = colour_map or {}

    def get_colour(self, value: Any) -> str:
        if value is None:
            return _DEFAULT_COLOUR
        return self._colour_map.get(str(value), _DEFAULT_COLOUR)
