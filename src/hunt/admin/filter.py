from __future__ import annotations

from typing import Any

from hunt.support.str import Str


class Filter:
    """Base class for admin index filters."""

    name: str = "Filter"
    attribute: str | None = None
    filter_type: str = "select"  # select | boolean | date_range

    def options(self) -> list:
        """Return options as dicts {"label": ..., "value": ...} or (value, label) tuples."""
        return []

    def normalized_options(self) -> list[dict]:
        """Normalize options() output to a uniform list of {"value", "label"} dicts."""
        result = []
        for opt in self.options():
            if isinstance(opt, dict):
                result.append(opt)
            elif isinstance(opt, (list, tuple)) and len(opt) >= 2:
                result.append({"value": str(opt[0]), "label": str(opt[1])})
        return result

    def apply(self, query: Any, value: Any) -> Any:
        """Apply this filter to a QueryBuilder and return the modified builder."""
        raise NotImplementedError(f"{type(self).__name__} must implement apply()")

    @classmethod
    def slug(cls) -> str:
        return Str.snake(cls.name).replace(" ", "_").lower()


class SelectFilter(Filter):
    """A filter that presents a <select> dropdown."""

    filter_type: str = "select"

    def apply(self, query: Any, value: Any) -> Any:
        if self.attribute and value not in (None, "", []):
            return query.where(self.attribute, value)
        return query


class BooleanFilter(Filter):
    """A filter that presents a yes/no toggle."""

    filter_type: str = "boolean"

    def options(self) -> list[dict]:
        return [
            {"label": "Yes", "value": "1"},
            {"label": "No", "value": "0"},
        ]

    def apply(self, query: Any, value: Any) -> Any:
        if self.attribute and value not in (None, ""):
            bool_val = value in ("1", "true", True, 1)
            return query.where(self.attribute, int(bool_val))
        return query


class DateRangeFilter(Filter):
    """A filter that presents from/to date inputs.

    Query params: ``filter_<slug>_from`` and ``filter_<slug>_to``.
    ``apply()`` receives ``{"from": str | None, "to": str | None}``.
    """

    filter_type: str = "date_range"

    def apply(self, query: Any, value: Any) -> Any:
        if not self.attribute:
            return query
        if isinstance(value, dict):
            from_val = value.get("from")
            to_val = value.get("to")
        else:
            return query
        if from_val:
            query = query.where(self.attribute, ">=", from_val)
        if to_val:
            query = query.where(self.attribute, "<=", to_val)
        return query


class TrashedFilter(Filter):
    """Filter to show, hide, or exclusively show soft-deleted records."""

    name: str = "Trashed"
    filter_type: str = "select"

    def options(self) -> list[dict]:
        return [
            {"label": "Without Trashed", "value": "without"},
            {"label": "With Trashed", "value": "with"},
            {"label": "Only Trashed", "value": "only"},
        ]

    def apply(self, query: Any, value: Any) -> Any:
        if value == "with":
            return query.with_trashed()
        if value == "only":
            return query.with_trashed().where_not_null("deleted_at")
        # "without" or default — soft-delete exclusion is handled automatically
        return query
