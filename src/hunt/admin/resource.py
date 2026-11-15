from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from hunt.support.str import Str

if TYPE_CHECKING:
    from hunt.admin.action import Action
    from hunt.admin.field import Field
    from hunt.admin.filter import Filter


_SEARCH_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$")
_UNSORTABLE_FIELD_TYPES = frozenset({"has_many", "belongs_to_many", "media_browser", "password"})


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards with a backslash (fallback when the builder has no ``where_like``)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AdminResource:
    """
    Base class for Hunt admin resources (akin to Laravel Nova's Resource).
    Subclasses must set `model` and implement `fields()`.
    """

    model: type  # set on subclass
    label: str | None = None
    label_plural: str | None = None
    title_attribute: str | None = None
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
        if self.title_attribute:
            val = instance._attributes.get(self.title_attribute)
            return str(val) if val is not None else ""
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
        """Return the set of column names that may be used for sorting.

        Only fields explicitly marked ``.sortable()`` qualify, plus the default
        order column. Relation fields are never sortable (their attribute is
        not a column on this table) and neither are sensitive fields.
        """
        cols: set[str] = set()
        try:
            for f in self.fields():
                if getattr(f, "_sortable", False) is not True:
                    continue
                if getattr(f, "sensitive", False) is True:
                    continue
                if getattr(f, "field_type", "") in _UNSORTABLE_FIELD_TYPES:
                    continue
                attribute = getattr(f, "attribute", None)
                if isinstance(attribute, str) and attribute:
                    cols.add(attribute)
        except Exception:
            pass
        cols.add(self.default_order[0])
        return cols

    def apply_search(self, query: Any, search_term: str) -> Any:
        """Apply LIKE search across `search_columns` as one AND-ed group."""
        return self._search_group(query, search_term)

    def _search_group(self, query: Any, search_term: Any) -> Any:
        """AND ``(col0 LIKE ? OR col1 LIKE ? ...)`` onto ``query``.

        The OR-ed columns are always wrapped in a single group so the search
        can never widen the query past its other constraints (filters,
        soft-delete scope, a parent foreign key, an ``index_query`` override).
        """
        term = str(search_term or "")
        if not term or not self.search_columns:
            return query
        columns = [c for c in self.search_columns if isinstance(c, str) and _SEARCH_IDENT_RE.match(c)]
        if not columns:
            return query

        def _group(q: Any) -> Any:
            # Prefer the query builder's escape-aware helpers when it has them.
            if callable(getattr(type(q), "where_like", None)) and callable(getattr(type(q), "or_where_like", None)):
                for i, col in enumerate(columns):
                    q = q.where_like(col, term) if i == 0 else q.or_where_like(col, term)
                return q
            pattern = "%" + _escape_like(term) + "%"
            for i, col in enumerate(columns):
                q = q.where(col, "LIKE", pattern) if i == 0 else q.or_where(col, "LIKE", pattern)
            return q

        return query.where_group(_group)

    def apply_filters(self, query: Any, request: Any) -> Any:
        """Apply each registered Filter using its query-string value."""
        from hunt.admin.filter import DateRangeFilter

        for f in self.filters():
            try:
                if isinstance(f, DateRangeFilter):
                    slug = f.slug()
                    from_val = request.query(f"filter_{slug}_from", None) or None
                    to_val = request.query(f"filter_{slug}_to", None) or None
                    if from_val or to_val:
                        query = f.apply(query, {"from": from_val, "to": to_val})
                else:
                    value = request.query(f"filter_{f.slug()}", None)
                    if value not in (None, ""):
                        query = f.apply(query, value)
            except Exception:
                pass
        return query
