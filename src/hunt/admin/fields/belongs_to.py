from __future__ import annotations

from hunt.admin.field import Field


class BelongsTo(Field):
    field_type: str = "belongs_to"

    def __init__(
        self,
        name: str,
        related_resource_class: type,
        attribute: str | None = None,
    ) -> None:
        super().__init__(name, attribute)
        self.related_resource_class = related_resource_class
        self._searchable: bool = False

    def searchable(self) -> "BelongsTo":
        self._searchable = True
        return self
