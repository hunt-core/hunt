from __future__ import annotations

from hunt.admin.field import Field


class HasMany(Field):
    field_type: str = "has_many"

    def __init__(
        self,
        name: str,
        related_resource_class: type,
        foreign_key: str | None = None,
        attribute: str | None = None,
    ) -> None:
        super().__init__(name, attribute)
        self.related_resource_class = related_resource_class
        self.foreign_key = foreign_key
        # HasMany panels are never displayed in these views
        self.hide_from_index()
        self.hide_from_create()
        self.hide_from_edit()
