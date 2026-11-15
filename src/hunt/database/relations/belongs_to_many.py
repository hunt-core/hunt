from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from hunt.database.connection import connection, use_connection
from hunt.database.identifiers import simple_ident as _ident

if TYPE_CHECKING:
    from hunt.database.model import Model
    from hunt.database.query_builder import QueryBuilder


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

    def _joined_query(self) -> QueryBuilder:
        """The related model's own query (soft-delete visibility and global
        scopes included) joined to the pivot table."""
        related = self._related
        pivot = self._pivot_table
        return related.query().join(
            pivot,
            f"{pivot}.{self._related_key}",
            f"{related.table}.{related.primary_key}",
        )

    def get_results(self) -> list:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        qb = self._joined_query().select_raw(f"{_ident(self._related.table)}.*")
        qb = qb._add_scope_clause("basic", (f"{self._pivot_table}.{self._foreign_key}", "=", parent_id))
        return qb.get()

    def eager_load_for(
        self,
        rel_name: str,
        instances: list[Model],
        constraint: Any = None,
    ) -> None:
        fk = self._foreign_key
        pivot = self._pivot_table
        pk = instances[0].primary_key if instances else "id"

        parent_ids = list({inst._attributes.get(pk) for inst in instances if inst._attributes.get(pk) is not None})
        if not parent_ids:
            for inst in instances:
                inst._relations[rel_name] = []
            return

        pivot_col = f"_pivot_{fk}"
        qb = self._joined_query().select_raw(f"{_ident(self._related.table)}.*", f"{pivot}.{fk} AS {pivot_col}")
        # The parent-key restriction is a scope predicate: a constraint that
        # uses or_where cannot widen it to other parents' rows.
        qb = qb._add_scope_clause("basic", (f"{pivot}.{fk}", "IN", parent_ids))
        if constraint:
            qb = constraint(qb)

        grouped: dict[Any, list] = {}
        for instance in qb.get():
            pivot_fk_val = instance._attributes.pop(pivot_col, None)
            instance._original.pop(pivot_col, None)
            grouped.setdefault(pivot_fk_val, []).append(instance)

        for inst in instances:
            inst._relations[rel_name] = grouped.get(inst._attributes.get(pk), [])

    def attach(self, related_id: Any, pivot_data: dict | None = None) -> None:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        data = {self._foreign_key: parent_id, self._related_key: related_id}
        for k, v in (pivot_data or {}).items():
            data[_ident(k)] = v
        columns = ", ".join(data.keys())
        placeholders = ", ".join(f":_v{j}" for j in range(len(data)))
        bindings = {f"_v{j}": v for j, v in enumerate(data.values())}
        sql = f"INSERT INTO {self._pivot_table} ({columns}) VALUES ({placeholders})"
        with use_connection(connection(self._parent._connection)) as conn:
            conn.execute(text(sql), bindings)

    def detach(self, related_id: Any | None = None) -> None:
        parent_id = self._parent._attributes.get(self._parent.primary_key)
        if related_id is not None:
            sql = f"DELETE FROM {self._pivot_table} WHERE {self._foreign_key} = :pid AND {self._related_key} = :rid"
            bindings = {"pid": parent_id, "rid": related_id}
        else:
            sql = f"DELETE FROM {self._pivot_table} WHERE {self._foreign_key} = :pid"
            bindings = {"pid": parent_id}
        with use_connection(connection(self._parent._connection)) as conn:
            conn.execute(text(sql), bindings)

    def sync(self, related_ids: list) -> None:
        """Replace the attached set with *related_ids* atomically.

        The detach and the attaches run in one transaction, so an error
        part-way through leaves the previous pivot rows in place.
        """
        from hunt.database.connection import begin

        with begin(self._parent._connection):
            self.detach()
            for related_id in related_ids:
                self.attach(related_id)
