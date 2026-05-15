from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from hunt.support.str import Str

if TYPE_CHECKING:
    from hunt.admin.action import Action
    from hunt.admin.field import Field
    from hunt.admin.filter import Filter


class AdminResource:
    """
    Base class for Hunt admin resources (akin to Laravel Nova's Resource).
    Subclasses must set `model` and implement `fields()`.
    """

    model: type  # set on subclass
    label: str | None = None
    label_plural: str | None = None
    per_page: int = 10
    per_page_options: ClassVar[list] = [10, 25, 50, 100]
    icon: str = (
        "M3.375 19.5h17.25m-17.25 0a1.125 1.125 0 0 1-1.125-1.125M3.375 19.5h1.5"
        "C5.496 19.5 6 18.996 6 18.375m-3.75 0V5.625m0 12.75v-1.5c0-.621.504-1.125"
        " 1.125-1.125m18.375 2.625V5.625m0 12.75c0 .621-.504 1.125-1.125 1.125h-1.5"
        "m3.375-13.5A1.125 1.125 0 0 0 20.625 4.5h-1.5m3.375 1.125v12m-15 0h7.5"
        "m-7.5 0a1.125 1.125 0 0 1-1.125-1.125M4.5 18.375V5.625m0 12.75h1.5"
        "m12 0h1.5m-1.5 0a1.125 1.125 0 0 1-1.125-1.125M18 5.625v12.75M6 5.625h12"
    )
    search_columns: ClassVar[list[str]] = []
    default_order: tuple = ("id", "desc")

    # ------------------------------------------------------------------
    # Field / action / filter / metric declarations
    # ------------------------------------------------------------------

    def fields(self) -> list[Field]:
        raise NotImplementedError(f"{type(self).__name__} must implement fields()")

    def filters(self) -> list[Filter]:
        return []

    def actions(self) -> list[Action]:
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
        return False

    def can_create(self, request: Any) -> bool:
        return False

    def can_update(self, request: Any, instance: Any = None) -> bool:
        return False

    def can_delete(self, request: Any, instance: Any = None) -> bool:
        return False

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
        allowed_sort_cols = self._sortable_columns()
        if sort_col and sort_col in allowed_sort_cols and sort_dir in ("asc", "desc"):
            try:
                query = query.order_by(sort_col, sort_dir)
            except ValueError:
                query = query.order_by(self.default_order[0], self.default_order[1])
        else:
            query = query.order_by(self.default_order[0], self.default_order[1])
        return query

    def _sortable_columns(self) -> set[str]:
        """Return the set of column names that may be used for sorting."""
        cols: set[str] = set()
        try:
            for f in self.fields():
                if hasattr(f, "attribute") and f.attribute:
                    cols.add(f.attribute)
        except Exception:
            pass
        cols.add(self.default_order[0])
        return cols

    def apply_search(self, query: Any, search_term: str) -> Any:
        """Apply LIKE search across `search_columns`."""
        if not self.search_columns:
            return query
        # Escape LIKE wildcards in user input to prevent wildcard injection
        escaped = search_term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        first_col = self.search_columns[0]
        try:
            query = query.where(first_col, "LIKE", f"%{escaped}%")
        except ValueError:
            return query
        for col in self.search_columns[1:]:
            try:
                query = query.or_where(col, "LIKE", f"%{escaped}%")
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
