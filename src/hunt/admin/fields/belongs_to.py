from __future__ import annotations

from typing import Any

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

    def searchable(self) -> BelongsTo:
        self._searchable = True
        return self

    def display_value(self, instance: Any) -> str:
        cache = getattr(instance, "_relation_cache", None)
        if cache is not None and self.attribute in cache:
            cached = cache[self.attribute]
            return str(cached) if cached is not None else ""
        val = instance._attributes.get(self.attribute)
        return str(val) if val is not None else ""

    def get_options(self, limit: int = 500) -> list[tuple]:
        """Return [(id, title), ...] for the related model — used in non-searchable form selects."""
        try:
            related_resource = self.related_resource_class()
            items = related_resource.model.query().limit(limit).get()
            return [(item._attributes.get("id"), related_resource.title(item)) for item in items]
        except Exception:
            return []
