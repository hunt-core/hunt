from __future__ import annotations

from hunt.admin.field import Field


class Textarea(Field):
    field_type: str = "textarea"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._rows: int = 4
        self.hide_from_index()

    def rows(self, n: int) -> Textarea:
        self._rows = n
        return self
