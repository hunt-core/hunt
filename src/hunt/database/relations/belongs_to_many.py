from __future__ import annotations
from typing import TYPE_CHECKING

from sqlalchemy import text
from hunt.database.connection import connection

if TYPE_CHECKING:
    from hunt.database.model import Model


class BelongsToMany:
    def __init__(
        self,
        related: type,
        parent: "Model",
        pivot_table: str,
        foreign_key: str,
        related_key: str,
    ) -> None:
        self._related = related
        self._parent = parent
        self._pivot_table = pivot_table
        self._foreign_key = foreign_key
        self._related_key = related_key

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
            rows = [dict(zip(keys, row)) for row in result.fetchall()]

        instances = []
        for row in rows:
            instance = self._related.__new__(self._related)
            instance._attributes = row
            instance._original = dict(row)
            instance._exists = True
            instance._relations = {}
            instances.append(instance)
        return instances

    def attach(self, related_id: Any, pivot_data: dict | None = None) -> None:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        data = {self._foreign_key: parent_id, self._related_key: related_id, **(pivot_data or {})}
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
