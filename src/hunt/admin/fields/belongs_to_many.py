from __future__ import annotations

from typing import Any

from hunt.admin.field import Field


class BelongsToMany(Field):
    field_type: str = "belongs_to_many"

    def __init__(
        self,
        name: str,
        related_resource_class: type,
        relation_method: str,
        attribute: str | None = None,
    ) -> None:
        super().__init__(name, attribute or relation_method)
        self.related_resource_class = related_resource_class
        self.relation_method = relation_method
        self.hide_from_index()

    def get_current_items(self, instance: Any) -> list[tuple]:
        """Return [(id, title), ...] for the currently related records."""
        try:
            related_resource = self.related_resource_class()
            rel = getattr(instance, self.relation_method)()
            items = rel.get_results()
            return [(str(item._attributes.get("id")), related_resource.title(item)) for item in items]
        except Exception:
            return []

    def get_options(self, limit: int = 500) -> list[tuple]:
        """Return [(id, title), ...] for all records of the related model."""
        try:
            related_resource = self.related_resource_class()
            items = related_resource.model.query().limit(limit).get()
            return [(str(item._attributes.get("id")), related_resource.title(item)) for item in items]
        except Exception:
            return []
