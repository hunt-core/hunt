from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from hunt.database.connection import connection

if TYPE_CHECKING:
    from hunt.database.model import Model

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


class BelongsToMany:
    def __init__(
        self,
        related: type,
        parent: Model,
        pivot_table: str,
        foreign_key: str,
        related_key: str,
    ) -> None:
        self._related = related
        self._parent = parent
        self._pivot_table = _ident(pivot_table)
        self._foreign_key = _ident(foreign_key)
        self._related_key = _ident(related_key)

    def get_results(self) -> list:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        related_table = self._related.table
        sql = (
            f"SELECT {related_table}.* FROM {related_table} "
            f"INNER JOIN {self._pivot_table} ON "
            f"{self._pivot_table}.{self._related_key} = {related_table}.{self._related.primary_key} "
            f"WHERE {self._pivot_table}.{self._foreign_key} = :parent_id"
        )
        engine = connection(self._parent._connection)
        with engine.connect() as conn:
            result = conn.execute(text(sql), {"parent_id": parent_id})
            keys = list(result.keys())
            rows = [dict(zip(keys, row, strict=False)) for row in result.fetchall()]

        instances = []
        for row in rows:
            instance = self._related.__new__(self._related)
            instance._attributes = row
            instance._original = dict(row)
            instance._exists = True
            instance._relations = {}
            instances.append(instance)
        return instances

    def eager_load_for(
        self,
        rel_name: str,
        instances: list[Model],
        constraint: Any = None,
    ) -> None:
        related_table = self._related.table
        fk = self._foreign_key
        rk = self._related_key
        pivot = self._pivot_table
        related = self._related
        pk = instances[0].primary_key if instances else "id"
        conn_name = getattr(instances[0], "_connection", None) if instances else None

        parent_ids = list({inst._attributes.get(pk) for inst in instances if inst._attributes.get(pk) is not None})
        if not parent_ids:
            for inst in instances:
                inst._relations[rel_name] = []
            return

        placeholders = ", ".join(f":p{i}" for i in range(len(parent_ids)))
        sql = (
            f"SELECT {related_table}.*, {pivot}.{fk} AS _pivot_{fk} "
            f"FROM {related_table} "
            f"INNER JOIN {pivot} ON {pivot}.{rk} = {related_table}.{related.primary_key} "
            f"WHERE {pivot}.{fk} IN ({placeholders})"
        )
        bindings = {f"p{i}": pid for i, pid in enumerate(parent_ids)}

        engine = connection(conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(sql), bindings)
            keys = list(result.keys())
            rows = [dict(zip(keys, row, strict=False)) for row in result.fetchall()]

        pivot_col = f"_pivot_{fk}"
        grouped: dict[Any, list] = {}
        for row in rows:
            pivot_fk_val = row.pop(pivot_col, None)
            instance = related.__new__(related)
            instance._attributes = row
            instance._original = dict(row)
            instance._exists = True
            instance._relations = {}
            grouped.setdefault(pivot_fk_val, []).append(instance)

        for inst in instances:
            inst._relations[rel_name] = grouped.get(inst._attributes.get(pk), [])

    def attach(self, related_id: Any, pivot_data: dict | None = None) -> None:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        extra = {_ident(k): v for k, v in (pivot_data or {}).items()}
        data = {self._foreign_key: parent_id, self._related_key: related_id, **extra}
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":{k}" for k in data.keys())
        sql = f"INSERT INTO {self._pivot_table} ({columns}) VALUES ({placeholders})"
        engine = connection(self._parent._connection)
        with engine.connect() as conn:
            conn.execute(text(sql), data)
            conn.commit()

    def detach(self, related_id: Any | None = None) -> None:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        if related_id is not None:
            sql = f"DELETE FROM {self._pivot_table} WHERE {self._foreign_key} = :pid AND {self._related_key} = :rid"
            bindings = {"pid": parent_id, "rid": related_id}
        else:
            sql = f"DELETE FROM {self._pivot_table} WHERE {self._foreign_key} = :pid"
            bindings = {"pid": parent_id}
        engine = connection(self._parent._connection)
        with engine.connect() as conn:
            conn.execute(text(sql), bindings)
            conn.commit()
