from __future__ import annotations

import contextvars
import functools
import inspect
import time
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

from sqlalchemy import text

from hunt.database.connection import _RELATION_PROBE, connection, use_connection
from hunt.database.identifiers import qualified_ident

# When True, relationship helper methods return the Relation object instead of
# executing the query. Used internally by the eager-loading infrastructure.
_EAGER_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar("eager_mode", default=False)


async def _run_sync(fn: Callable, /, *args: Any, **kwargs: Any) -> Any:
    """Run a sync callable in the default thread-pool executor.

    Yields control to the event loop while the blocking DB call executes in a
    worker thread, keeping the ASGI worker non-blocking under concurrent load.

    The caller's contextvars are copied into the worker thread, so global
    scopes that read a ContextVar (current user, tenant), the ambient
    transaction and the debug query log all behave exactly as in a sync call.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    return await loop.run_in_executor(None, functools.partial(ctx.run, fn, *args, **kwargs))


_VALID_DIRECTIONS = frozenset({"ASC", "DESC"})
_VALID_OPERATORS = frozenset(
    {
        "=",
        "!=",
        "<>",
        "<",
        ">",
        "<=",
        ">=",
        "LIKE",
        "NOT LIKE",
        "ILIKE",
        "NOT ILIKE",
        "IS NULL",
        "IS NOT NULL",
        "IN",
        "NOT IN",
        "BETWEEN",
    }
)
_VALID_JOIN_TYPES = frozenset({"INNER", "LEFT", "RIGHT", "CROSS"})

# Escape character for where_like(). Deliberately not a backslash: MySQL treats
# a backslash inside a string literal as an escape, so ``ESCAPE '\'`` would
# need different spellings per dialect. ``!`` is spelled the same everywhere.
_LIKE_ESCAPE = "!"


class _Missing:
    """Sentinel telling a two-argument ``where(col, value)`` from ``where(col, op, None)``."""

    _instance: ClassVar[_Missing | None] = None

    def __new__(cls) -> _Missing:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<missing>"

    def __bool__(self) -> bool:
        return False


_MISSING: Any = _Missing()


def _ident(name: str) -> str:
    return qualified_ident(name)


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards in a raw search term (for use with ``ESCAPE '!'``)."""
    e = _LIKE_ESCAPE
    return str(term).replace(e, e + e).replace("%", e + "%").replace("_", e + "_")


def _parse_condition(column: str, operator_or_value: Any, value: Any) -> tuple[str, str, Any]:
    """Normalise the (column, [operator,] value) arguments of where/or_where/having."""
    col = _ident(column)
    if operator_or_value is _MISSING:
        raise TypeError("A value (or an operator and a value) is required.")
    if value is _MISSING:
        op, val = "=", operator_or_value
    else:
        op = operator_or_value.upper() if isinstance(operator_or_value, str) else operator_or_value
        if not isinstance(op, str) or op not in _VALID_OPERATORS:
            raise ValueError(f"Invalid SQL operator: {op!r}")
        val = value

    if op in ("IS NULL", "IS NOT NULL"):
        return col, op, None
    if val is None:
        # ``col = NULL`` is never true in SQL; say what the caller means.
        if op == "=":
            return col, "IS NULL", None
        if op in ("!=", "<>"):
            return col, "IS NOT NULL", None
        raise ValueError(f"Operator {op!r} cannot be compared with None on column {column!r}.")
    if op in ("IN", "NOT IN"):
        if isinstance(val, (str, bytes, dict)) or not hasattr(val, "__iter__"):
            raise ValueError(f"Operator {op} requires a list of values for column {column!r}.")
        return col, op, list(val)
    if op == "BETWEEN":
        if isinstance(val, (str, bytes, dict)) or not hasattr(val, "__iter__"):
            raise ValueError(f"Operator BETWEEN requires a [start, end] pair for column {column!r}.")
        pair = list(val)
        if len(pair) != 2:
            raise ValueError(f"Operator BETWEEN requires exactly two values for column {column!r}.")
        return col, op, pair
    return col, op, val


class _RenderContext:
    """Bind-parameter allocator shared by every clause of one statement."""

    def __init__(self, builder: QueryBuilder) -> None:
        self.bindings: dict[str, Any] = {}
        self._counters: dict[str, int] = {}
        self._builder = builder
        self._dialect: str | None = None

    def bind(self, value: Any, prefix: str = "w") -> str:
        n = self._counters.get(prefix, 0)
        self._counters[prefix] = n + 1
        key = f"{prefix}{n}"
        self.bindings[key] = value
        return key

    def group_key(self, prefix: str = "w") -> str:
        n = self._counters.get(prefix, 0)
        self._counters[prefix] = n + 1
        return f"{prefix}{n}"

    @property
    def dialect(self) -> str:
        if self._dialect is None:
            self._dialect = self._builder._dialect_name()
        return self._dialect


class QueryBuilder:
    # Upper bound applied to ``per_page`` by paginate() / simple_paginate(), so
    # a request cannot ask for an unbounded page (``?per_page=10000000``).
    max_per_page: ClassVar[int] = 1000

    def __init__(self, table: str, model_class: type | None = None, conn_name: str | None = None) -> None:
        self._table = _ident(table)
        self._model_class = model_class
        self._conn_name = conn_name
        # User WHERE clauses in call order: (boolean, kind, payload).
        #   boolean: "AND" | "OR"  (how the clause joins the ones before it)
        #   kind:    "basic" (col, op, value) | "raw" (sql, bindings) |
        #            "group" callback | "like" (col, term) | "sub" [clauses]
        self._clauses: list[tuple[str, str, Any]] = []
        # Scope predicates (global scopes, relation key constraints). Always
        # ANDed with the whole user clause group, so no or_where can escape them.
        self._scope_clauses: list[tuple[str, str, Any]] = []
        self._order_bys: list[tuple[str, str]] = []
        self._group_bys: list[str] = []
        self._havings: list[tuple[str, str, Any]] = []
        self._limit_val: int | None = None
        self._offset_val: int | None = None
        self._selects: list[str] = ["*"]
        self._without: list[str] = []
        self._distinct = False
        self._with_trashed = False
        self._only_trashed = False
        self._joins: list[str] = []
        self._withs: dict[str, Callable | None] = {}
        self._global_scope_fns: dict[str, Any] = {}
        self._skip_global_scopes: set[str] = set()
        self._skip_all_global_scopes: bool = False
        self._remember: bool = False
        self._remember_ttl: int | None = None
        self._remember_key: str | None = None
        self._result_cache: list[Any] | None = None

    # ------------------------------------------------------------------
    # Cloning
    # ------------------------------------------------------------------

    def _clone(self) -> QueryBuilder:
        qb = QueryBuilder(self._table, self._model_class, self._conn_name)
        qb._clauses = list(self._clauses)
        qb._scope_clauses = list(self._scope_clauses)
        qb._order_bys = list(self._order_bys)
        qb._group_bys = list(self._group_bys)
        qb._havings = list(self._havings)
        qb._limit_val = self._limit_val
        qb._offset_val = self._offset_val
        qb._selects = list(self._selects)
        qb._without = list(self._without)
        qb._distinct = self._distinct
        qb._with_trashed = self._with_trashed
        qb._only_trashed = self._only_trashed
        qb._joins = list(self._joins)
        qb._withs = dict(self._withs)
        qb._global_scope_fns = dict(self._global_scope_fns)
        qb._skip_global_scopes = set(self._skip_global_scopes)
        qb._skip_all_global_scopes = self._skip_all_global_scopes
        qb._remember = self._remember
        qb._remember_ttl = self._remember_ttl
        qb._remember_key = self._remember_key
        return qb

    def _add_clause(self, boolean: str, kind: str, payload: Any) -> QueryBuilder:
        qb = self._clone()
        qb._clauses.append((boolean, kind, payload))
        return qb

    def _add_scope_clause(self, kind: str, payload: Any) -> QueryBuilder:
        qb = self._clone()
        qb._scope_clauses.append(("AND", kind, payload))
        return qb

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def where(self, column: str, operator_or_value: Any = _MISSING, value: Any = _MISSING) -> QueryBuilder:
        """Add an AND condition.

        ``where("a", 1)`` is ``a = 1``; ``where("a", ">", 1)`` uses the operator.
        A ``None`` value becomes ``IS NULL`` (for ``=``) or ``IS NOT NULL`` (for
        ``!=`` / ``<>``); any other operator with ``None`` raises ``ValueError``.
        """
        return self._add_clause("AND", "basic", _parse_condition(column, operator_or_value, value))

    def or_where(self, column: str, operator_or_value: Any = _MISSING, value: Any = _MISSING) -> QueryBuilder:
        """Add an OR condition.

        Clauses combine in call order with normal SQL precedence (AND binds
        tighter than OR): ``where(a).or_where(b).where(c)`` is ``a OR (b AND c)``.
        Soft-delete visibility, global scopes and relation constraints are
        always ANDed with the whole group and cannot be bypassed by an OR.
        """
        return self._add_clause("OR", "basic", _parse_condition(column, operator_or_value, value))

    def where_in(self, column: str, values: list) -> QueryBuilder:
        return self._add_clause("AND", "basic", _parse_condition(column, "IN", values))

    def where_not_in(self, column: str, values: list) -> QueryBuilder:
        return self._add_clause("AND", "basic", _parse_condition(column, "NOT IN", values))

    def where_null(self, column: str) -> QueryBuilder:
        return self._add_clause("AND", "basic", (_ident(column), "IS NULL", None))

    def where_not_null(self, column: str) -> QueryBuilder:
        return self._add_clause("AND", "basic", (_ident(column), "IS NOT NULL", None))

    def where_between(self, column: str, start: Any, end: Any) -> QueryBuilder:
        return self.where(column, ">=", start).where(column, "<=", end)

    def where_like(self, column: str, term: Any) -> QueryBuilder:
        """AND a "contains" search for a raw, user-supplied *term*.

        ``%``, ``_`` and the escape character in *term* are escaped, the term
        is wrapped in ``%...%`` and the LIKE carries an explicit ``ESCAPE``
        clause that is valid on SQLite, MySQL and PostgreSQL.  Matching is
        case-insensitive on all three (``ILIKE`` is used on PostgreSQL).
        """
        return self._add_clause("AND", "like", (_ident(column), "" if term is None else str(term)))

    def or_where_like(self, column: str, term: Any) -> QueryBuilder:
        """OR variant of :meth:`where_like`."""
        return self._add_clause("OR", "like", (_ident(column), "" if term is None else str(term)))

    def where_raw(self, sql: str, bindings: list | dict | None = None) -> QueryBuilder:
        # WARNING: sql is injected verbatim. Use bindings for all user-supplied values; never interpolate them into sql.
        if isinstance(bindings, (list, tuple)):
            return self._add_clause("AND", "raw", (sql, list(bindings)))
        return self._add_clause("AND", "raw", (sql, dict(bindings or {})))

    def when(self, condition: Any, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        """Apply callback to the query only when condition is truthy."""
        if condition:
            return callback(self)
        return self

    def where_group(self, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        """AND a grouped sub-clause: `.where_group(lambda q: q.where("a", 1).or_where("b", 2))`."""
        return self._add_clause("AND", "group", callback)

    def or_where_group(self, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        """OR a grouped sub-clause against the clauses before it."""
        return self._add_clause("OR", "group", callback)

    # ------------------------------------------------------------------
    # Joins
    # ------------------------------------------------------------------

    def join(self, table: str, first: str, second: str, join_type: str = "INNER") -> QueryBuilder:
        jt = join_type.upper()
        if jt not in _VALID_JOIN_TYPES:
            raise ValueError(f"Invalid join type: {join_type!r}")
        qb = self._clone()
        qb._joins.append(f"{jt} JOIN {_ident(table)} ON {_ident(first)} = {_ident(second)}")
        return qb

    def left_join(self, table: str, first: str, second: str) -> QueryBuilder:
        return self.join(table, first, second, "LEFT")

    def right_join(self, table: str, first: str, second: str) -> QueryBuilder:
        return self.join(table, first, second, "RIGHT")

    # ------------------------------------------------------------------
    # Ordering / grouping / limiting
    # ------------------------------------------------------------------

    def order_by(self, column: str, direction: str = "asc") -> QueryBuilder:
        qb = self._clone()
        col = _ident(column)
        dir_upper = direction.upper()
        if dir_upper not in _VALID_DIRECTIONS:
            raise ValueError(f"Invalid ORDER BY direction: {direction!r}")
        qb._order_bys.append((col, dir_upper))
        return qb

    def latest(self, column: str = "created_at") -> QueryBuilder:
        return self.order_by(column, "desc")

    def oldest(self, column: str = "created_at") -> QueryBuilder:
        return self.order_by(column, "asc")

    def group_by(self, *columns: str) -> QueryBuilder:
        qb = self._clone()
        qb._group_bys.extend(_ident(c) for c in columns)
        return qb

    def having(self, column: str, operator_or_value: Any = _MISSING, value: Any = _MISSING) -> QueryBuilder:
        qb = self._clone()
        qb._havings.append(_parse_condition(column, operator_or_value, value))
        return qb

    def limit(self, n: int) -> QueryBuilder:
        n = int(n)
        if n < 0:
            raise ValueError(f"limit() must not be negative, got {n}.")
        qb = self._clone()
        qb._limit_val = n
        return qb

    def take(self, n: int) -> QueryBuilder:
        return self.limit(n)

    def offset(self, n: int) -> QueryBuilder:
        n = int(n)
        if n < 0:
            raise ValueError(f"offset() must not be negative, got {n}.")
        qb = self._clone()
        qb._offset_val = n
        return qb

    def skip(self, n: int) -> QueryBuilder:
        return self.offset(n)

    def select(self, *columns: str) -> QueryBuilder:
        qb = self._clone()
        qb._selects = [_ident(c) for c in columns]
        return qb

    def only(self, *columns: str) -> QueryBuilder:
        return self.select(*columns)

    def without(self, *columns: str) -> QueryBuilder:
        qb = self._clone()
        qb._without = [_ident(c) for c in columns]
        return qb

    def select_raw(self, *expressions: str) -> QueryBuilder:
        # WARNING: expressions are injected verbatim into SQL. Never pass user input here.
        qb = self._clone()
        qb._selects = list(expressions)
        return qb

    def distinct(self) -> QueryBuilder:
        qb = self._clone()
        qb._distinct = True
        return qb

    def with_trashed(self) -> QueryBuilder:
        qb = self._clone()
        qb._with_trashed = True
        return qb

    def only_trashed(self) -> QueryBuilder:
        qb = self._clone()
        qb._with_trashed = True
        qb._only_trashed = True
        return qb

    # ------------------------------------------------------------------
    # Eager loading
    # ------------------------------------------------------------------

    def with_(self, *relations: str | dict) -> QueryBuilder:
        """Specify relations to eager-load.

        Accepts string names or dicts mapping name → constraint callable::

            Post.with_("author", "comments")
            Post.with_({"comments": lambda q: q.where("approved", 1)})

        Names must be relationship methods defined on the model. Anything else
        (``"delete"``, ``"save"``, a private or inherited framework method)
        raises ``ValueError`` before any query runs.
        """
        qb = self._clone()
        for rel in relations:
            if isinstance(rel, dict):
                for name, constraint in rel.items():
                    qb._withs[name] = constraint
            else:
                qb._withs[rel] = None
        if qb._model_class is not None:
            for name in qb._withs:
                _relation_method(qb._model_class, name)
        return qb

    # ------------------------------------------------------------------
    # Query scopes
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if not name.startswith("_"):
            model_cls = self.__dict__.get("_model_class")
            if model_cls is not None:
                scope_fn = getattr(model_cls, f"scope_{name}", None)
                if scope_fn is not None:

                    def _invoke(*args: Any, **kwargs: Any) -> QueryBuilder:
                        return scope_fn(self, *args, **kwargs)

                    return _invoke
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    # ------------------------------------------------------------------
    # Global scope control
    # ------------------------------------------------------------------

    def without_global_scope(self, *names: str) -> QueryBuilder:
        qb = self._clone()
        qb._skip_global_scopes.update(names)
        return qb

    def without_global_scopes(self) -> QueryBuilder:
        qb = self._clone()
        qb._skip_all_global_scopes = True
        return qb

    def _materialize(self) -> QueryBuilder:
        """Apply pending global scopes and return an execution-ready QueryBuilder.

        The WHERE clauses a scope adds are moved into the scope bucket as one
        parenthesised group, so they are ANDed with the caller's whole clause
        list and an ``or_where`` in user code cannot widen them.
        """
        qb = self._clone()
        scopes = qb._global_scope_fns
        qb._global_scope_fns = {}
        if not scopes or qb._skip_all_global_scopes:
            return qb
        skip = set(qb._skip_global_scopes)
        for name, scope_fn in scopes.items():
            if name in skip:
                continue
            before = qb._clauses
            scoped = scope_fn(qb)
            if not isinstance(scoped, QueryBuilder):
                raise TypeError(f"Global scope {name!r} must return the QueryBuilder it was given.")
            n = len(before)
            after = scoped._clauses
            if len(after) >= n and all(a is b for a, b in zip(after[:n], before, strict=True)):
                added = after[n:]
                if scoped is qb:
                    scoped = qb._clone()
                scoped._clauses = list(before)
                if added:
                    scoped._scope_clauses.append(("AND", "sub", list(added)))
            scoped._global_scope_fns = {}
            qb = scoped
        return qb

    # ------------------------------------------------------------------
    # Query caching
    # ------------------------------------------------------------------

    def remember(self, ttl: int | None, key: str | None = None) -> QueryBuilder:
        """Cache the result of get() / first() for *ttl* seconds.

        ``ttl=None`` caches forever; a ttl of zero or less means "do not cache".
        """
        qb = self._clone()
        qb._remember = ttl is None or ttl > 0
        qb._remember_ttl = ttl
        qb._remember_key = key
        return qb

    def _derive_cache_key(self) -> str:
        import hashlib

        from hunt.database.connection import _default_name

        sql, bindings = self._build_select()
        withs = sorted(self._withs.keys())
        # The connection name is part of the key: the same SQL against two
        # databases (tenants, replicas) must not share a cache entry.
        conn_name = self._conn_name or _default_name()
        payload = f"{conn_name}|{self._table}|{sql}|{sorted(bindings.items(), key=lambda kv: kv[0])}|{withs}"
        return "qb:" + hashlib.sha256(payload.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Collection protocol — makes QueryBuilder usable as a list directly.
    # The rows are fetched once per builder object and reused, so
    # ``if posts: ... for p in posts: ... len(posts)`` runs one query, not three.
    # Call get() for a guaranteed-fresh read.
    # ------------------------------------------------------------------

    def _results(self) -> list[Any]:
        if self._result_cache is None:
            self._result_cache = self.get()
        return self._result_cache

    def __iter__(self) -> Iterator[Any]:
        return iter(self._results())

    def __len__(self) -> int:
        return len(self._results())

    def __bool__(self) -> bool:
        return bool(self._results())

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def get(self) -> list[Any]:
        mat = self._materialize()
        if mat._withs and mat._model_class:
            # Reject anything that is not a relationship before running a query.
            for rel_name in mat._withs:
                _relation_method(mat._model_class, rel_name)
        if mat._remember:
            from hunt.cache.manager import Cache

            cache_key = mat._remember_key or mat._derive_cache_key()
            cached_rows = Cache.get(cache_key)
            if cached_rows is not None:
                results = [mat._hydrate(row) for row in cached_rows]
                if results and mat._withs and mat._model_class:
                    mat._eager_load(results)
                return results
            sql, bindings = mat._build_select()
            rows = mat._execute(sql, bindings)
            results = [mat._hydrate(row) for row in rows]
            if results and mat._withs and mat._model_class:
                mat._eager_load(results)
            Cache.put(cache_key, rows, mat._remember_ttl)
            return results
        sql, bindings = mat._build_select()
        rows = mat._execute(sql, bindings)
        results = [mat._hydrate(row) for row in rows]
        if results and mat._withs and mat._model_class:
            mat._eager_load(results)
        return results

    def first(self) -> Any | None:
        results = self.limit(1).get()
        return results[0] if results else None

    def first_or_fail(self) -> Any:
        result = self.first()
        if result is None:
            from hunt.http.response import HttpException

            raise HttpException(404, "Record not found.")
        return result

    def _primary_key(self) -> str:
        return getattr(self._model_class, "primary_key", None) or "id"

    def find(self, id: Any) -> Any | None:
        return self.where(self._primary_key(), id).first()

    def find_or_fail(self, id: Any) -> Any:
        return self.where(self._primary_key(), id).first_or_fail()

    def _without_paging(self) -> QueryBuilder:
        qb = self._clone()
        qb._order_bys = []
        qb._limit_val = None
        qb._offset_val = None
        return qb

    def count(self) -> int:
        mat = self._materialize()
        base = mat._without_paging()
        if mat._group_bys or mat._distinct or mat._havings:
            # COUNT(*) on a grouped / distinct query counts rows per group (or
            # ignores DISTINCT). Count the rows of the query itself instead.
            inner_sql, bindings = base._build_select()
            sql = f"SELECT COUNT(*) AS _count FROM ({inner_sql}) AS _sub"
        else:
            sql, bindings = base.select_raw("COUNT(*) as _count")._build_select()
        rows = mat._execute(sql, bindings)
        if rows:
            row = rows[0]
            return int(row["_count"] if isinstance(row, dict) else row[0])
        return 0

    def exists(self) -> bool:
        return self.count() > 0

    def _aggregate(self, fn: str, column: str) -> Any:
        col = _ident(column)
        mat = self._materialize()
        qb = mat._without_paging().select_raw(f"{fn}({col}) as _agg")
        rows = mat._execute(*qb._build_select())
        return rows[0]["_agg"] if rows else None

    def min(self, column: str) -> Any:
        return self._aggregate("MIN", column)

    def max(self, column: str) -> Any:
        return self._aggregate("MAX", column)

    def avg(self, column: str) -> float | None:
        val = self._aggregate("AVG", column)
        return float(val) if val is not None else None

    def sum(self, column: str) -> Any:
        return self._aggregate("SUM", column)

    def pluck(self, column: str) -> list[Any]:
        """Return a flat list of values for a single column."""
        col = _ident(column)
        mat = self._materialize()
        qb = mat.select_raw(col)
        qb._model_class = None
        rows = mat._execute(*qb._build_select())
        # The driver reports ``posts.title`` as ``title``.
        key = col.rsplit(".", 1)[-1]
        return [row.get(key) for row in rows]

    def _page_args(self, per_page: Any, page: Any) -> tuple[int, int]:
        """Clamp untrusted paging input: page >= 1, 1 <= per_page <= max_per_page."""
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(per_page)
        except (TypeError, ValueError):
            per_page = 15
        return min(max(1, per_page), max(1, int(self.max_per_page))), max(1, page)

    def paginate(self, per_page: int = 15, page: int = 1):
        from hunt.pagination import PaginationResult

        per_page, page = self._page_args(per_page, page)
        total = self.count()
        items = self.offset((page - 1) * per_page).limit(per_page).get()
        last_page = max(1, (total + per_page - 1) // per_page)
        return PaginationResult(
            data=items,
            total=total,
            per_page=per_page,
            current_page=page,
            last_page=last_page,
        )

    def simple_paginate(self, per_page: int = 15, page: int = 1):
        """Paginate without a COUNT query — fetches one extra row to detect next page."""
        from hunt.pagination import PaginationResult

        per_page, page = self._page_args(per_page, page)
        items = self.offset((page - 1) * per_page).limit(per_page + 1).get()
        has_more = len(items) > per_page
        return PaginationResult(
            data=items[:per_page],
            total=None,
            per_page=per_page,
            current_page=page,
            last_page=None,
            has_more_pages=has_more,
        )

    def _stable_order(self) -> QueryBuilder:
        """OFFSET paging needs a total order, or rows are skipped / repeated.

        When the caller gave no ORDER BY and the builder knows its model, order
        by the primary key. (A model-less builder has no known key: add an
        explicit ``order_by`` before chunking it.)
        """
        if self._order_bys or self._model_class is None:
            return self
        return self.order_by(f"{self._table}.{self._primary_key()}")

    def chunk(self, size: int, callback: Callable[[list[Any]], bool | None]) -> bool:
        """Iterate results in chunks. Callback receives each chunk.
        Return False from callback to stop early."""
        size = int(size)
        if size < 1:
            raise ValueError(f"chunk() size must be at least 1, got {size}.")
        ordered = self._stable_order()
        page = 1
        while True:
            results = ordered.offset((page - 1) * size).limit(size).get()
            if not results:
                break
            if callback(results) is False:
                return False
            if len(results) < size:
                break
            page += 1
        return True

    def each(self, callback: Callable[[Any, int], bool | None]) -> bool:
        """Iterate every row one at a time. Callback receives (item, index).
        Return False to stop early."""
        index = 0
        page = 1
        chunk_size = 500
        ordered = self._stable_order()
        while True:
            results = ordered.offset((page - 1) * chunk_size).limit(chunk_size).get()
            if not results:
                break
            for item in results:
                if callback(item, index) is False:
                    return False
                index += 1
            if len(results) < chunk_size:
                break
            page += 1
        return True

    # ------------------------------------------------------------------
    # Async execution — wraps every sync terminal method in run_in_executor
    # so ASGI controller actions can `await` DB calls without blocking the
    # event loop.  Sync callers (CLI, migrations, seeds) are unaffected.
    # ------------------------------------------------------------------

    async def async_get(self) -> list[Any]:
        return await _run_sync(self.get)

    async def async_first(self) -> Any | None:
        return await _run_sync(self.first)

    async def async_first_or_fail(self) -> Any:
        return await _run_sync(self.first_or_fail)

    async def async_find(self, id: Any) -> Any | None:
        return await _run_sync(self.find, id)

    async def async_count(self) -> int:
        return await _run_sync(self.count)

    async def async_exists(self) -> bool:
        return await _run_sync(self.exists)

    async def async_min(self, column: str) -> Any:
        return await _run_sync(self.min, column)

    async def async_max(self, column: str) -> Any:
        return await _run_sync(self.max, column)

    async def async_avg(self, column: str) -> float | None:
        return await _run_sync(self.avg, column)

    async def async_sum(self, column: str) -> Any:
        return await _run_sync(self.sum, column)

    async def async_pluck(self, column: str) -> list[Any]:
        return await _run_sync(self.pluck, column)

    async def async_paginate(self, per_page: int = 15, page: int = 1) -> Any:
        return await _run_sync(self.paginate, per_page, page)

    async def async_insert(self, data: dict, returning: str | None = None) -> Any:
        return await _run_sync(self.insert, data, returning)

    async def async_insert_many(self, rows: list[dict]) -> None:
        return await _run_sync(self.insert_many, rows)

    async def async_update(self, data: dict) -> int:
        return await _run_sync(self.update, data)

    async def async_delete(self) -> int:
        return await _run_sync(self.delete)

    async def async_force_delete(self) -> int:
        return await _run_sync(self.force_delete)

    # ------------------------------------------------------------------
    # Writes. Bind names are positional (``:_v0``); a column name is never
    # interpolated into a bind name, only validated into the column list.
    # ------------------------------------------------------------------

    def _dialect_name(self) -> str:
        return connection(self._conn_name).dialect.name

    def _write(self, sql: str, bindings: dict) -> Any:
        """Run a write on the ambient transaction, or on its own committed connection."""
        from hunt.database.debug import timed_execute

        with use_connection(connection(self._conn_name)) as conn:
            return timed_execute(conn, text(sql), bindings)

    def insert(self, data: dict, returning: str | None = None) -> Any:
        from hunt.database.debug import timed_execute

        if not data:
            raise ValueError("insert() requires at least one column.")
        cols = [_ident(k) for k in data]
        columns = ", ".join(cols)
        placeholders = ", ".join(f":_v{j}" for j in range(len(cols)))
        bindings = {f"_v{j}": v for j, v in enumerate(data.values())}
        sql = f"INSERT INTO {self._table} ({columns}) VALUES ({placeholders})"
        engine = connection(self._conn_name)
        is_pg = engine.dialect.name == "postgresql"
        if is_pg and returning:
            sql += f" RETURNING {_ident(returning)}"
        with use_connection(engine) as conn:
            result = timed_execute(conn, text(sql), bindings)
            if is_pg and returning:
                row = result.fetchone()
                return row[0] if row else None
            return result.lastrowid

    def insert_get_id(self, data: dict, key: str = "id") -> Any:
        return self.insert(data, returning=key)

    def insert_many(self, rows: list[dict]) -> None:
        """Bulk-insert rows in a single SQL statement.

        Every row must have exactly the same keys as the first one (in any
        order); a row with a missing, extra or malformed key raises ``ValueError``.
        """
        if not rows:
            return
        keys = list(rows[0].keys())
        cols = [_ident(k) for k in keys]
        if not cols:
            raise ValueError("insert_many() requires at least one column.")
        key_set = set(keys)
        columns = ", ".join(cols)
        placeholders_list = []
        bindings: dict[str, Any] = {}
        for i, row in enumerate(rows):
            if len(row) != len(keys) or set(row.keys()) != key_set:
                raise ValueError(f"insert_many(): row {i} does not have the same columns as row 0.")
            placeholders_list.append("(" + ", ".join(f":_r{i}_{j}" for j in range(len(keys))) + ")")
            for j, k in enumerate(keys):
                bindings[f"_r{i}_{j}"] = row[k]
        sql = f"INSERT INTO {self._table} ({columns}) VALUES {', '.join(placeholders_list)}"
        self._write(sql, bindings)

    def update(self, data: dict) -> int:
        if not data:
            raise ValueError("update() requires at least one column to set.")
        mat = self._materialize()
        where_clause, bindings = mat._build_where_clause()
        sets = []
        for j, (k, v) in enumerate(data.items()):
            sets.append(f"{_ident(k)} = :_set_{j}")
            bindings[f"_set_{j}"] = v
        sql = f"UPDATE {mat._table} SET {', '.join(sets)}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        return mat._write(sql, bindings).rowcount

    def _adjust(self, column: str, amount: Any, extra: dict | None, sign: str) -> int:
        col = _ident(column)
        # Global scopes apply here exactly as they do to update().
        mat = self._materialize()
        where_clause, bindings = mat._build_where_clause()
        sets = [f"{col} = {col} {sign} :_adj_amount"]
        bindings["_adj_amount"] = amount
        for j, (k, v) in enumerate((extra or {}).items()):
            sets.append(f"{_ident(k)} = :_adj_{j}")
            bindings[f"_adj_{j}"] = v
        sql = f"UPDATE {mat._table} SET {', '.join(sets)}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        return mat._write(sql, bindings).rowcount

    def increment(self, column: str, amount: int = 1, extra: dict | None = None) -> int:
        return self._adjust(column, amount, extra, "+")

    def decrement(self, column: str, amount: int = 1, extra: dict | None = None) -> int:
        return self._adjust(column, amount, extra, "-")

    def delete(self) -> int:
        """Delete the matching rows.

        On a model that uses ``SoftDeletes`` this sets ``deleted_at`` (the rows
        stay in the table); use :meth:`force_delete` to remove them for real.
        """
        if self._model_class is not None and getattr(self._model_class, "_soft_deletes", False):
            return self.update({"deleted_at": int(time.time())})
        return self.force_delete()

    def force_delete(self) -> int:
        """Permanently delete the matching rows, even on a SoftDeletes model."""
        mat = self._materialize()
        where_clause, bindings = mat._build_where_clause()
        sql = f"DELETE FROM {mat._table}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        return mat._write(sql, bindings).rowcount

    def restore(self) -> int:
        """Clear ``deleted_at`` on the matching soft-deleted rows."""
        if self._model_class is None or not getattr(self._model_class, "_soft_deletes", False):
            return 0
        return self.only_trashed().update({"deleted_at": None})

    # ------------------------------------------------------------------
    # SQL building
    # ------------------------------------------------------------------

    def _columns_for_table(self) -> list[str]:
        from sqlalchemy import inspect as sa_inspect

        try:
            with use_connection(connection(self._conn_name), commit=False) as conn:
                return [col["name"] for col in sa_inspect(conn).get_columns(self._table)]
        except Exception:
            return []

    def _build_select(self) -> tuple[str, dict]:
        distinct = "DISTINCT " if self._distinct else ""
        selects = self._selects
        if self._without and selects == ["*"]:
            exclude = set(self._without)
            all_cols = self._columns_for_table()
            selects = [c for c in all_cols if c not in exclude] or ["*"]
        cols = ", ".join(selects)
        sql = f"SELECT {distinct}{cols} FROM {self._table}"
        for join in self._joins:
            sql += f" {join}"
        ctx = _RenderContext(self)
        where_clause, bindings = self._build_where_clause(ctx)
        if where_clause:
            sql += f" WHERE {where_clause}"
        if self._group_bys:
            sql += f" GROUP BY {', '.join(self._group_bys)}"
        if self._havings:
            having_parts = [self._render_basic(col, op, val, ctx, "h") for col, op, val in self._havings]
            sql += f" HAVING {' AND '.join(having_parts)}"
        if self._order_bys:
            order = ", ".join(f"{col} {dir}" for col, dir in self._order_bys)
            sql += f" ORDER BY {order}"
        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"
        elif self._offset_val is not None and ctx.dialect in ("mysql", "sqlite"):
            # MySQL and SQLite reject OFFSET without LIMIT.
            sql += " LIMIT 18446744073709551615" if ctx.dialect == "mysql" else " LIMIT -1"
        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"
        return sql, bindings

    def _build_where_clause(self, ctx: _RenderContext | None = None) -> tuple[str, dict]:
        """Render ``<scope predicates> AND (<user clauses>)``.

        Scope predicates (soft-delete visibility, global scopes, relation key
        constraints) are always ANDed with the user's whole clause list.
        """
        ctx = ctx or _RenderContext(self)
        parts: list[str] = []

        # Soft-delete visibility
        if self._model_class and getattr(self._model_class, "_soft_deletes", False):
            if self._only_trashed:
                parts.append(f"{self._table}.deleted_at IS NOT NULL")
            elif not self._with_trashed:
                parts.append(f"{self._table}.deleted_at IS NULL")

        for _boolean, kind, payload in self._scope_clauses:
            fragment = self._render_clause(kind, payload, ctx)
            if fragment:
                parts.append(fragment)

        user_sql, has_or = self._render_clauses(self._clauses, ctx)
        if user_sql:
            parts.append(f"({user_sql})" if has_or and parts else user_sql)

        return " AND ".join(parts), ctx.bindings

    def _render_clauses(self, clauses: list[tuple[str, str, Any]], ctx: _RenderContext) -> tuple[str, bool]:
        """Render clauses in call order. Returns (sql, contains_top_level_or)."""
        sql = ""
        has_or = False
        for boolean, kind, payload in clauses:
            fragment = self._render_clause(kind, payload, ctx)
            if not fragment:
                continue
            if not sql:
                sql = fragment
            else:
                sql = f"{sql} {boolean} {fragment}"
                has_or = has_or or boolean == "OR"
        return sql, has_or

    def _render_clause(self, kind: str, payload: Any, ctx: _RenderContext) -> str:
        if kind == "basic":
            col, op, val = payload
            return self._render_basic(col, op, val, ctx)
        if kind == "like":
            col, term = payload
            key = ctx.bind(f"%{_escape_like(term)}%")
            if ctx.dialect == "postgresql":
                # ILIKE keeps the search case-insensitive like the other two
                # dialects; the cast lets it run against non-text columns.
                return f"CAST({col} AS TEXT) ILIKE :{key} ESCAPE '{_LIKE_ESCAPE}'"
            return f"{col} LIKE :{key} ESCAPE '{_LIKE_ESCAPE}'"
        if kind == "raw":
            raw_sql, raw_bindings = payload
            if isinstance(raw_bindings, list):
                group = ctx.group_key("raw_")
                for i, v in enumerate(raw_bindings):
                    key = f"{group}_{i}"
                    raw_sql = raw_sql.replace("?", f":{key}", 1)
                    ctx.bindings[key] = v
            else:
                ctx.bindings.update(raw_bindings)
            return f"({raw_sql})"
        if kind == "group":
            sub = payload(QueryBuilder(self._table, self._model_class, self._conn_name))
            if not isinstance(sub, QueryBuilder):
                raise TypeError("A where_group() callback must return the QueryBuilder it was given.")
            sub_sql, _ = self._render_clauses(sub._clauses, ctx)
            return f"({sub_sql})" if sub_sql else ""
        if kind == "sub":
            sub_sql, _ = self._render_clauses(payload, ctx)
            return f"({sub_sql})" if sub_sql else ""
        raise ValueError(f"Unknown clause kind: {kind!r}")

    def _render_basic(self, col: str, op: str, val: Any, ctx: _RenderContext, prefix: str = "w") -> str:
        """Render one ``column operator value`` condition (shared by WHERE and HAVING)."""
        if op in ("IS NULL", "IS NOT NULL"):
            return f"{col} {op}"
        if op in ("IN", "NOT IN"):
            values = list(val)
            if not values:
                # ``IN ()`` is a syntax error; an empty list matches nothing.
                ctx.group_key(prefix)
                return "1 = 0" if op == "IN" else "1 = 1"
            group = ctx.group_key(prefix)
            names = []
            for j, v in enumerate(values):
                key = f"{group}_{j}"
                ctx.bindings[key] = v
                names.append(f":{key}")
            return f"{col} {op} ({', '.join(names)})"
        if op == "BETWEEN":
            start, end = val
            group = ctx.group_key(prefix)
            ctx.bindings[f"{group}_0"] = start
            ctx.bindings[f"{group}_1"] = end
            return f"{col} BETWEEN :{group}_0 AND :{group}_1"
        if op in ("ILIKE", "NOT ILIKE") and ctx.dialect != "postgresql":
            # Only PostgreSQL has ILIKE; fold case on both sides elsewhere.
            key = ctx.bind(val, prefix)
            like = "LIKE" if op == "ILIKE" else "NOT LIKE"
            return f"LOWER({col}) {like} LOWER(:{key})"
        key = ctx.bind(val, prefix)
        return f"{col} {op} :{key}"

    def _execute(self, sql: str, bindings: dict) -> list[dict]:
        from hunt.database.debug import timed_execute

        with use_connection(connection(self._conn_name), commit=False) as conn:
            result = timed_execute(conn, text(sql), bindings)
            keys = list(result.keys())
            return [dict(zip(keys, row, strict=False)) for row in result.fetchall()]

    def _hydrate(self, row: dict) -> Any:
        if self._model_class is None:
            return row
        instance = self._model_class.__new__(self._model_class)
        instance._attributes = dict(row)
        instance._original = dict(row)
        instance._exists = True
        instance._relations = {}
        return instance

    # ------------------------------------------------------------------
    # Eager loading
    # ------------------------------------------------------------------

    def _eager_load(self, models: list) -> None:
        for rel_name, constraint in self._withs.items():
            rel_obj = _resolve_relation(models[0], rel_name)
            rel_obj.eager_load_for(rel_name, models, constraint)


# ---------------------------------------------------------------------------
# Relationship resolution for eager loading
# ---------------------------------------------------------------------------


def relation(fn: Callable) -> Callable:
    """Mark a model method as a relationship::

        class Post(Model):
            @relation
            def author(self):
                return self.belongs_to(User)

    Marking is optional, but once a model marks any relationship, ``with_()``
    accepts *only* its marked methods.
    """
    fn._hunt_relation = True  # type: ignore[attr-defined]
    return fn


def _relation_method(model_cls: type, rel_name: Any) -> Callable:
    """Return the function behind relationship *rel_name*, or raise ValueError.

    Runs no code. Only a plain instance method that takes no arguments and is
    defined on the application's model (not inherited from the framework's
    ``Model``, so never ``delete`` / ``save`` / ``restore``) is accepted.
    """
    from hunt.database.model import Model

    def _reject(reason: str) -> ValueError:
        return ValueError(f"{model_cls.__name__} has no relationship {rel_name!r}: {reason}.")

    if not isinstance(rel_name, str) or not rel_name.isidentifier() or rel_name.startswith("_"):
        raise _reject("not a valid relationship name")
    if inspect.getattr_static(Model, rel_name, None) is not None:
        raise _reject("it is a framework Model method")
    attr = inspect.getattr_static(model_cls, rel_name, None)
    if attr is None or not inspect.isfunction(attr):
        raise _reject("no such method")
    marked = getattr(attr, "_hunt_relation", False)
    if not marked and _has_marked_relations(model_cls):
        raise _reject("the model marks its relationships with @relation and this method is not one of them")
    try:
        params = list(inspect.signature(attr).parameters.values())[1:]
    except (TypeError, ValueError):
        params = []
    if any(p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params):
        raise _reject("relationship methods take no arguments")
    return attr


def _has_marked_relations(model_cls: type) -> bool:
    for klass in model_cls.__mro__:
        for value in vars(klass).values():
            if getattr(value, "_hunt_relation", False):
                return True
    return False


def _resolve_relation(model: Any, rel_name: str) -> Any:
    """Call a validated relationship method in eager mode and return its Relation.

    While the method runs, all database access through the framework is
    refused: a genuine relationship method only builds a Relation object, so a
    method that tries to read or write is not a relationship and is rejected
    without touching the database.
    """
    fn = _relation_method(type(model), rel_name)
    eager_token = _EAGER_MODE.set(True)
    probe_token = _RELATION_PROBE.set(True)
    try:
        rel_obj = fn(model)
    except Exception as exc:
        raise ValueError(f"{type(model).__name__}.{rel_name} is not a relationship method ({exc}).") from exc
    finally:
        _RELATION_PROBE.reset(probe_token)
        _EAGER_MODE.reset(eager_token)
    if not hasattr(rel_obj, "eager_load_for"):
        raise ValueError(f"{type(model).__name__}.{rel_name} is not a relationship method.")
    return rel_obj
