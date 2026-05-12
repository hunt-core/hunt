from __future__ import annotations

from typing import Any, TYPE_CHECKING

from hunt.support.str import Str

if TYPE_CHECKING:
    from hunt.admin.field import Field
    from hunt.admin.action import Action
    from hunt.admin.filter import Filter


class AdminResource:
    """
    Base class for Hunt admin resources (akin to Laravel Nova's Resource).
    Subclasses must set `model` and implement `fields()`.
    """

    model: type  # set on subclass
    label: str | None = None
    label_plural: str | None = None
    per_page: int = 15
    search_columns: list[str] = []
    default_order: tuple = ("id", "desc")

    # ------------------------------------------------------------------
    # Field / action / filter / metric declarations
    # ------------------------------------------------------------------

    def fields(self) -> list["Field"]:
        raise NotImplementedError(f"{type(self).__name__} must implement fields()")

    def filters(self) -> list["Filter"]:
        return []

    def actions(self) -> list["Action"]:
        return []

    def metrics(self) -> list:
        return []

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    @classmethod
    def slug(cls) -> str:
        """URL key, e.g. "posts" for a PostResource with model=Post."""
        return Str.plural(Str.snake(cls.model.__name__)).lower()

    @classmethod
    def get_label(cls) -> str:
        if cls.label:
            return cls.label
        return cls.model.__name__

    @classmethod
    def get_label_plural(cls) -> str:
        if cls.label_plural:
            return cls.label_plural
        return Str.plural(cls.get_label())

    # ------------------------------------------------------------------
    # Title
    # ------------------------------------------------------------------

    def title(self, instance: Any) -> str:
        attrs = instance._attributes
        for key in ("name", "title", "id"):
            val = attrs.get(key)
            if val is not None:
                return str(val)
        return str(attrs.get("id", ""))

    # ------------------------------------------------------------------
    # Authorization (override in subclasses as needed)
    # ------------------------------------------------------------------

    def can_view_any(self, request: Any) -> bool:
        return True

    def can_create(self, request: Any) -> bool:
        return True

    def can_update(self, request: Any, instance: Any = None) -> bool:
        return True

    def can_delete(self, request: Any, instance: Any = None) -> bool:
        return True

    # ------------------------------------------------------------------
    # Query building
    # ------------------------------------------------------------------

    def index_query(self, request: Any) -> Any:
        """Build the index query with search, filters, and ordering applied."""
        query = self.model.query()
        search_term = request.query("search", "")
        if search_term:
            query = self.apply_search(query, search_term)
        query = self.apply_filters(query, request)
        sort_col = request.query("sort", "")
        sort_dir = request.query("dir", "desc")
        if sort_col and sort_dir in ("asc", "desc"):
            try:
                query = query.order_by(sort_col, sort_dir)
            except ValueError:
                query = query.order_by(self.default_order[0], self.default_order[1])
        else:
            query = query.order_by(self.default_order[0], self.default_order[1])
        return query

    def apply_search(self, query: Any, search_term: str) -> Any:
        """Apply LIKE search across `search_columns`."""
        if not self.search_columns:
            return query
        first_col = self.search_columns[0]
        try:
            query = query.where(first_col, "LIKE", f"%{search_term}%")
        except ValueError:
            return query
        for col in self.search_columns[1:]:
            try:
                query = query.or_where(col, "LIKE", f"%{search_term}%")
            except ValueError:
                continue
        return query

    def apply_filters(self, query: Any, request: Any) -> Any:
        """Apply each registered Filter using its query-string value."""
        for f in self.filters():
            key = f"filter_{f.slug()}"
            value = request.query(key, None)
            if value not in (None, ""):
                try:
                    query = f.apply(query, value)
                except Exception:
                    pass
        return query
