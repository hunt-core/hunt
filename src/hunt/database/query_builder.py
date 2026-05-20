from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from hunt.database.connection import connection

if TYPE_CHECKING:
    pass

# Allowlist for SQL identifiers (table/column names)
_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$")
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


def _ident(name: str) -> str:
    if name == "*":
        return name
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid SQL identifier {name!r}. Only alphanumeric characters and "
            "underscores are allowed, with optional table.column qualification."
        )
    return name


class QueryBuilder:
    def __init__(self, table: str, model_class: type | None = None, conn_name: str | None = None) -> None:
        self._table = _ident(table)
        self._model_class = model_class
        self._conn_name = conn_name
        self._wheres: list[tuple[str, str, Any]] = []
        self._or_wheres: list[tuple[str, str, Any]] = []
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
        self._raw_wheres: list[tuple[str, dict]] = []
        self._nested_wheres: list[tuple[str, Callable[[QueryBuilder], QueryBuilder]]] = []

    # ------------------------------------------------------------------
    # Cloning
    # ------------------------------------------------------------------

    def _clone(self) -> QueryBuilder:
        qb = QueryBuilder(self._table, self._model_class, self._conn_name)
        qb._wheres = list(self._wheres)
        qb._or_wheres = list(self._or_wheres)
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
        qb._raw_wheres = list(self._raw_wheres)
        qb._nested_wheres = list(self._nested_wheres)
        return qb

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def where(self, column: str, operator_or_value: Any, value: Any = None) -> QueryBuilder:
        qb = self._clone()
        col = _ident(column)
        if value is None:
            qb._wheres.append((col, "=", operator_or_value))
        else:
            op = operator_or_value.upper() if isinstance(operator_or_value, str) else operator_or_value
            if op not in _VALID_OPERATORS:
                raise ValueError(f"Invalid SQL operator: {op!r}")
            qb._wheres.append((col, op, value))
        return qb

    def or_where(self, column: str, operator_or_value: Any, value: Any = None) -> QueryBuilder:
        qb = self._clone()
        col = _ident(column)
        if value is None:
            qb._or_wheres.append((col, "=", operator_or_value))
        else:
            op = operator_or_value.upper() if isinstance(operator_or_value, str) else operator_or_value
            if op not in _VALID_OPERATORS:
                raise ValueError(f"Invalid SQL operator: {op!r}")
            qb._or_wheres.append((col, op, value))
        return qb

    def where_in(self, column: str, values: list) -> QueryBuilder:
        qb = self._clone()
        qb._wheres.append((_ident(column), "IN", values))
        return qb

    def where_not_in(self, column: str, values: list) -> QueryBuilder:
        qb = self._clone()
        qb._wheres.append((_ident(column), "NOT IN", values))
        return qb

    def where_null(self, column: str) -> QueryBuilder:
        qb = self._clone()
        qb._wheres.append((_ident(column), "IS NULL", None))
        return qb

    def where_not_null(self, column: str) -> QueryBuilder:
        qb = self._clone()
        qb._wheres.append((_ident(column), "IS NOT NULL", None))
        return qb

    def where_between(self, column: str, start: Any, end: Any) -> QueryBuilder:
        return self.where(column, ">=", start).where(column, "<=", end)

    def where_raw(self, sql: str, bindings: list | dict | None = None) -> QueryBuilder:
        # WARNING: sql is injected verbatim. Use bindings for all user-supplied values; never interpolate them into sql.
        qb = self._clone()
        if isinstance(bindings, list):
            named: dict = {f"raw_{len(qb._raw_wheres)}_{i}": v for i, v in enumerate(bindings)}
            raw_sql = sql
            for key in named:
                raw_sql = raw_sql.replace("?", f":{key}", 1)
            qb._raw_wheres.append((raw_sql, named))
        else:
            qb._raw_wheres.append((sql, bindings or {}))
        return qb

    def when(self, condition: Any, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        """Apply callback to the query only when condition is truthy."""
        if condition:
            return callback(self)
        return self

    def where_group(self, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        """AND a grouped sub-clause: `.where_group(lambda q: q.where("a", 1).or_where("b", 2))`."""
        qb = self._clone()
        qb._nested_wheres.append(("AND", callback))
        return qb

    def or_where_group(self, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        """OR a grouped sub-clause against the rest of the WHERE clause."""
        qb = self._clone()
        qb._nested_wheres.append(("OR", callback))
        return qb

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

    def having(self, column: str, operator_or_value: Any, value: Any = None) -> QueryBuilder:
        qb = self._clone()
        col = _ident(column)
        if value is None:
            qb._havings.append((col, "=", operator_or_value))
        else:
            op = operator_or_value.upper() if isinstance(operator_or_value, str) else operator_or_value
            if op not in _VALID_OPERATORS:
                raise ValueError(f"Invalid SQL operator: {op!r}")
            qb._havings.append((col, op, value))
        return qb

    def limit(self, n: int) -> QueryBuilder:
        qb = self._clone()
        qb._limit_val = n
        return qb

    def take(self, n: int) -> QueryBuilder:
        return self.limit(n)

    def offset(self, n: int) -> QueryBuilder:
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
        """
        qb = self._clone()
        for rel in relations:
            if isinstance(rel, dict):
                for name, constraint in rel.items():
                    qb._withs[name] = constraint
            else:
                qb._withs[rel] = None
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
    # Collection protocol — makes QueryBuilder usable as a list directly
    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[Any]:
        return iter(self.get())

    def __len__(self) -> int:
        return self.count()

    def __bool__(self) -> bool:
        return self.exists()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def get(self) -> list[Any]:
        sql, bindings = self._build_select()
        rows = self._execute(sql, bindings)
        results = [self._hydrate(row) for row in rows]
        if results and self._withs and self._model_class:
            self._eager_load(results)
        return results

    def first(self) -> Any | None:
        results = self.limit(1).get()
        return results[0] if results else None

    def first_or_fail(self) -> Any:
        result = self.first()
        if result is None:
            from hunt.http.exceptions import HttpException

            raise HttpException(404, "Record not found.")
        return result

    def find(self, id: Any) -> Any | None:
        return self.where("id", id).first()

    def count(self) -> int:
        qb = self.select_raw("COUNT(*) as _count")
        qb._order_bys = []
        qb._limit_val = None
        qb._offset_val = None
        sql, bindings = qb._build_select()
        rows = self._execute(sql, bindings)
        if rows:
            row = rows[0]
            return int(row["_count"] if isinstance(row, dict) else row[0])
        return 0

    def exists(self) -> bool:
        return self.count() > 0

    def min(self, column: str) -> Any:
        col = _ident(column)
        qb = self.select_raw(f"MIN({col}) as _agg")
        qb._order_bys = []
        qb._limit_val = None
        qb._offset_val = None
        rows = self._execute(*qb._build_select())
        return rows[0]["_agg"] if rows else None

    def max(self, column: str) -> Any:
        col = _ident(column)
        qb = self.select_raw(f"MAX({col}) as _agg")
        qb._order_bys = []
        qb._limit_val = None
        qb._offset_val = None
        rows = self._execute(*qb._build_select())
        return rows[0]["_agg"] if rows else None

    def avg(self, column: str) -> float | None:
        col = _ident(column)
        qb = self.select_raw(f"AVG({col}) as _agg")
        qb._order_bys = []
        qb._limit_val = None
        qb._offset_val = None
        rows = self._execute(*qb._build_select())
        val = rows[0]["_agg"] if rows else None
        return float(val) if val is not None else None

    def sum(self, column: str) -> Any:
        col = _ident(column)
        qb = self.select_raw(f"SUM({col}) as _agg")
        qb._order_bys = []
        qb._limit_val = None
        qb._offset_val = None
        rows = self._execute(*qb._build_select())
        return rows[0]["_agg"] if rows else None

    def pluck(self, column: str) -> list[Any]:
        """Return a flat list of values for a single column."""
        col = _ident(column)
        qb = self._clone()
        qb._selects = [col]
        qb._model_class = None
        rows = qb._execute(*qb._build_select())
        return [row.get(col) for row in rows]

    def paginate(self, per_page: int = 15, page: int = 1) -> dict:
        total = self.count()
        items = self.offset((page - 1) * per_page).limit(per_page).get()
        return {
            "data": items,
            "total": total,
            "per_page": per_page,
            "current_page": page,
            "last_page": max(1, (total + per_page - 1) // per_page),
        }

    def chunk(self, size: int, callback: Callable[[list[Any]], bool | None]) -> bool:
        """Iterate results in chunks. Callback receives each chunk.
        Return False from callback to stop early."""
        page = 1
        while True:
            results = self.offset((page - 1) * size).limit(size).get()
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
        while True:
            results = self.offset((page - 1) * chunk_size).limit(chunk_size).get()
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

    def insert(self, data: dict) -> Any:
        from hunt.database.debug import timed_execute

        cols = [_ident(k) for k in data.keys()]
        columns = ", ".join(cols)
        placeholders = ", ".join(f":{k}" for k in data.keys())
        sql = f"INSERT INTO {self._table} ({columns}) VALUES ({placeholders})"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = timed_execute(conn, text(sql), data)
            conn.commit()
            return result.lastrowid

    def insert_get_id(self, data: dict) -> Any:
        return self.insert(data)

    def insert_many(self, rows: list[dict]) -> None:
        """Bulk-insert rows in a single SQL statement."""
        if not rows:
            return
        cols = [_ident(k) for k in rows[0].keys()]
        columns = ", ".join(cols)
        placeholders_list = []
        bindings: dict[str, Any] = {}
        for i, row in enumerate(rows):
            ph = ", ".join(f":_r{i}_{k}" for k in row.keys())
            placeholders_list.append(f"({ph})")
            for k, v in row.items():
                bindings[f"_r{i}_{k}"] = v
        sql = f"INSERT INTO {self._table} ({columns}) VALUES {', '.join(placeholders_list)}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            from hunt.database.debug import timed_execute

            timed_execute(conn, text(sql), bindings)
            conn.commit()

    def update(self, data: dict) -> int:
        where_clause, bindings = self._build_where_clause()
        sets = ", ".join(f"{_ident(k)} = :_set_{k}" for k in data.keys())
        for k, v in data.items():
            bindings[f"_set_{k}"] = v
        sql = f"UPDATE {self._table} SET {sets}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            from hunt.database.debug import timed_execute

            result = timed_execute(conn, text(sql), bindings)
            conn.commit()
            return result.rowcount

    def increment(self, column: str, amount: int = 1, extra: dict | None = None) -> int:
        col = _ident(column)
        where_clause, bindings = self._build_where_clause()
        sets = [f"{col} = {col} + :_inc_amount"]
        bindings["_inc_amount"] = amount
        if extra:
            for k, v in extra.items():
                sets.append(f"{_ident(k)} = :_inc_{k}")
                bindings[f"_inc_{k}"] = v
        sql = f"UPDATE {self._table} SET {', '.join(sets)}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            from hunt.database.debug import timed_execute

            result = timed_execute(conn, text(sql), bindings)
            conn.commit()
            return result.rowcount

    def decrement(self, column: str, amount: int = 1, extra: dict | None = None) -> int:
        col = _ident(column)
        where_clause, bindings = self._build_where_clause()
        sets = [f"{col} = {col} - :_dec_amount"]
        bindings["_dec_amount"] = amount
        if extra:
            for k, v in extra.items():
                sets.append(f"{_ident(k)} = :_dec_{k}")
                bindings[f"_dec_{k}"] = v
        sql = f"UPDATE {self._table} SET {', '.join(sets)}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            from hunt.database.debug import timed_execute

            result = timed_execute(conn, text(sql), bindings)
            conn.commit()
            return result.rowcount

    def delete(self) -> int:
        where_clause, bindings = self._build_where_clause()
        sql = f"DELETE FROM {self._table}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            from hunt.database.debug import timed_execute

            result = timed_execute(conn, text(sql), bindings)
            conn.commit()
            return result.rowcount

    # ------------------------------------------------------------------
    # SQL building
    # ------------------------------------------------------------------

    def _columns_for_table(self) -> list[str]:
        from sqlalchemy import inspect as sa_inspect

        engine = connection(self._conn_name)
        try:
            return [col["name"] for col in sa_inspect(engine).get_columns(self._table)]
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
        where_clause, bindings = self._build_where_clause()
        if where_clause:
            sql += f" WHERE {where_clause}"
        if self._group_bys:
            sql += f" GROUP BY {', '.join(self._group_bys)}"
        if self._havings:
            having_parts = []
            for i, (col, op, val) in enumerate(self._havings):
                key = f"h{i}"
                bindings[key] = val
                having_parts.append(f"{col} {op} :{key}")
            sql += f" HAVING {' AND '.join(having_parts)}"
        if self._order_bys:
            order = ", ".join(f"{col} {dir}" for col, dir in self._order_bys)
            sql += f" ORDER BY {order}"
        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"
        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"
        return sql, bindings

    def _build_where_clause(self) -> tuple[str, dict]:
        parts: list[str] = []
        bindings: dict[str, Any] = {}

        # Soft-delete visibility
        if self._model_class:
            uses_soft_deletes = getattr(self._model_class, "_soft_deletes", False)
            if uses_soft_deletes:
                if self._only_trashed:
                    parts.append(f"{self._table}.deleted_at IS NOT NULL")
                elif not self._with_trashed:
                    parts.append(f"{self._table}.deleted_at IS NULL")

        for i, (col, op, val) in enumerate(self._wheres):
            key = f"w{i}"
            if op in ("IS NULL", "IS NOT NULL"):
                parts.append(f"{col} {op}")
            elif op in ("IN", "NOT IN"):
                placeholders = ", ".join(f":{key}_{j}" for j, _ in enumerate(val))
                for j, v in enumerate(val):
                    bindings[f"{key}_{j}"] = v
                parts.append(f"{col} {op} ({placeholders})")
            else:
                parts.append(f"{col} {op} :{key}")
                bindings[key] = val

        for raw_sql, raw_bindings in self._raw_wheres:
            parts.append(f"({raw_sql})")
            bindings.update(raw_bindings)

        or_parts: list[str] = []
        for i, (col, op, val) in enumerate(self._or_wheres):
            key = f"ow{i}"
            or_parts.append(f"{col} {op} :{key}")
            bindings[key] = val

        for i, (logic, callback) in enumerate(self._nested_wheres):
            sub = QueryBuilder(self._table, None, self._conn_name)
            sub = callback(sub)
            sub_clause, sub_bindings = sub._build_where_clause()
            if not sub_clause:
                continue
            prefix = f"_ng{i}_"
            prefixed = {prefix + k: v for k, v in sub_bindings.items()}
            for k in sorted(sub_bindings, key=len, reverse=True):
                sub_clause = sub_clause.replace(f":{k}", f":{prefix}{k}")
            bindings.update(prefixed)
            if logic == "AND":
                parts.append(f"({sub_clause})")
            else:
                or_parts.append(f"({sub_clause})")

        and_clause = " AND ".join(parts)

        if or_parts and and_clause:
            return f"({and_clause}) OR {' OR '.join(or_parts)}", bindings
        if or_parts:
            return " OR ".join(or_parts), bindings
        return and_clause, bindings

    def _execute(self, sql: str, bindings: dict) -> list[dict]:
        from hunt.database.debug import timed_execute

        engine = connection(self._conn_name)
        with engine.connect() as conn:
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
        from hunt.database.model import _EAGER_MODE

        for rel_name, constraint in self._withs.items():
            token = _EAGER_MODE.set(True)
            try:
                rel_obj = getattr(models[0], rel_name)()
            finally:
                _EAGER_MODE.reset(token)
            rel_obj.eager_load_for(rel_name, models, constraint)
