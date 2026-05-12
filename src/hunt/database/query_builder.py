from __future__ import annotations

import re
import time
from typing import Any, TYPE_CHECKING

from sqlalchemy import text

from hunt.database.connection import connection

if TYPE_CHECKING:
    from hunt.database.model import Model

# Allowlist for SQL identifiers (table/column names)
_IDENT_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?$')
_VALID_DIRECTIONS = frozenset({"ASC", "DESC"})
_VALID_OPERATORS = frozenset({
    "=", "!=", "<>", "<", ">", "<=", ">=",
    "LIKE", "NOT LIKE", "IS NULL", "IS NOT NULL", "IN", "NOT IN", "BETWEEN",
})


def _ident(name: str) -> str:
    """Validate a SQL identifier and return it unchanged, or raise ValueError."""
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
        self._limit_val: int | None = None
        self._offset_val: int | None = None
        self._selects: list[str] = ["*"]
        self._with_trashed = False
        self._joins: list[str] = []

    # ------------------------------------------------------------------
    # Cloning
    # ------------------------------------------------------------------

    def _clone(self) -> "QueryBuilder":
        qb = QueryBuilder(self._table, self._model_class, self._conn_name)
        qb._wheres = list(self._wheres)
        qb._or_wheres = list(self._or_wheres)
        qb._order_bys = list(self._order_bys)
        qb._limit_val = self._limit_val
        qb._offset_val = self._offset_val
        qb._selects = list(self._selects)
        qb._with_trashed = self._with_trashed
        qb._joins = list(self._joins)
        return qb

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def where(self, column: str, operator_or_value: Any, value: Any = None) -> "QueryBuilder":
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

    def or_where(self, column: str, operator_or_value: Any, value: Any = None) -> "QueryBuilder":
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

    def where_in(self, column: str, values: list) -> "QueryBuilder":
        qb = self._clone()
        qb._wheres.append((_ident(column), "IN", values))
        return qb

    def where_not_in(self, column: str, values: list) -> "QueryBuilder":
        qb = self._clone()
        qb._wheres.append((_ident(column), "NOT IN", values))
        return qb

    def where_null(self, column: str) -> "QueryBuilder":
        qb = self._clone()
        qb._wheres.append((_ident(column), "IS NULL", None))
        return qb

    def where_not_null(self, column: str) -> "QueryBuilder":
        qb = self._clone()
        qb._wheres.append((_ident(column), "IS NOT NULL", None))
        return qb

    def where_between(self, column: str, start: Any, end: Any) -> "QueryBuilder":
        return self.where(column, ">=", start).where(column, "<=", end)

    # ------------------------------------------------------------------
    # Ordering / limiting
    # ------------------------------------------------------------------

    def order_by(self, column: str, direction: str = "asc") -> "QueryBuilder":
        qb = self._clone()
        col = _ident(column)
        dir_upper = direction.upper()
        if dir_upper not in _VALID_DIRECTIONS:
            raise ValueError(f"Invalid ORDER BY direction: {direction!r}. Must be 'ASC' or 'DESC'.")
        qb._order_bys.append((col, dir_upper))
        return qb

    def latest(self, column: str = "created_at") -> "QueryBuilder":
        return self.order_by(column, "desc")

    def oldest(self, column: str = "created_at") -> "QueryBuilder":
        return self.order_by(column, "asc")

    def limit(self, n: int) -> "QueryBuilder":
        qb = self._clone()
        qb._limit_val = n
        return qb

    def take(self, n: int) -> "QueryBuilder":
        return self.limit(n)

    def offset(self, n: int) -> "QueryBuilder":
        qb = self._clone()
        qb._offset_val = n
        return qb

    def skip(self, n: int) -> "QueryBuilder":
        return self.offset(n)

    def select(self, *columns: str) -> "QueryBuilder":
        qb = self._clone()
        qb._selects = [_ident(c) for c in columns]
        return qb

    def select_raw(self, *expressions: str) -> "QueryBuilder":
        """Accept raw SQL expressions (e.g. COUNT(*) AS total). Caller is responsible for safety."""
        qb = self._clone()
        qb._selects = list(expressions)
        return qb

    def with_trashed(self) -> "QueryBuilder":
        qb = self._clone()
        qb._with_trashed = True
        return qb

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def get(self) -> list[Any]:
        sql, bindings = self._build_select()
        rows = self._execute(sql, bindings)
        return [self._hydrate(row) for row in rows]

    def first(self) -> Any | None:
        results = self.limit(1).get()
        return results[0] if results else None

    def find(self, id: Any) -> Any | None:
        return self.where("id", id).first()

    def count(self) -> int:
        old = self._selects
        qb = self.select_raw("COUNT(*) as _count")
        sql, bindings = qb._build_select()
        rows = self._execute(sql, bindings)
        if rows:
            row = rows[0]
            return int(row["_count"] if isinstance(row, dict) else row[0])
        return 0

    def exists(self) -> bool:
        return self.count() > 0

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

    def insert(self, data: dict) -> Any:
        cols = [_ident(k) for k in data.keys()]
        columns = ", ".join(cols)
        placeholders = ", ".join(f":{k}" for k in data.keys())
        sql = f"INSERT INTO {self._table} ({columns}) VALUES ({placeholders})"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(sql), data)
            conn.commit()
            return result.lastrowid

    def update(self, data: dict) -> int:
        set_clause, bindings = self._build_where_clause()
        sets = ", ".join(f"{_ident(k)} = :_set_{k}" for k in data.keys())
        for k, v in data.items():
            bindings[f"_set_{k}"] = v
        sql = f"UPDATE {self._table} SET {sets}"
        if set_clause:
            sql += f" WHERE {set_clause}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(sql), bindings)
            conn.commit()
            return result.rowcount

    def delete(self) -> int:
        where_clause, bindings = self._build_where_clause()
        sql = f"DELETE FROM {self._table}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(sql), bindings)
            conn.commit()
            return result.rowcount

    # ------------------------------------------------------------------
    # SQL building
    # ------------------------------------------------------------------

    def _build_select(self) -> tuple[str, dict]:
        cols = ", ".join(self._selects)
        sql = f"SELECT {cols} FROM {self._table}"
        for join in self._joins:
            sql += f" {join}"
        where_clause, bindings = self._build_where_clause()
        if where_clause:
            sql += f" WHERE {where_clause}"
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

        # Auto-exclude soft-deleted records
        if self._model_class and not self._with_trashed:
            uses_soft_deletes = getattr(self._model_class, "_soft_deletes", False)
            if uses_soft_deletes:
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

        and_clause = " AND ".join(parts)

        or_parts: list[str] = []
        for i, (col, op, val) in enumerate(self._or_wheres):
            key = f"ow{i}"
            or_parts.append(f"{col} {op} :{key}")
            bindings[key] = val

        if or_parts and and_clause:
            return f"({and_clause}) OR {' OR '.join(or_parts)}", bindings
        if or_parts:
            return " OR ".join(or_parts), bindings
        return and_clause, bindings

    def _execute(self, sql: str, bindings: dict) -> list[dict]:
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(sql), bindings)
            keys = list(result.keys())
            return [dict(zip(keys, row)) for row in result.fetchall()]

    def _hydrate(self, row: dict) -> Any:
        if self._model_class is None:
            return row
        instance = self._model_class.__new__(self._model_class)
        instance._attributes = dict(row)
        instance._original = dict(row)
        instance._exists = True
        return instance
