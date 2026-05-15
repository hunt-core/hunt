from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.database.model import Model


class BelongsTo:
    def __init__(self, related: type, child: Model, foreign_key: str, owner_key: str) -> None:
        self._related = related
        self._child = child
        self._foreign_key = foreign_key
        self._owner_key = owner_key

    def get_result(self) -> Any:
        fk_val = self._child._attributes.get(self._foreign_key)
        if fk_val is None:
            return None
        return self._related.query().where(self._owner_key, fk_val).first()

    def eager_load_for(
        self,
        rel_name: str,
        instances: list[Model],
        constraint: Any = None,
    ) -> None:
        fk = self._foreign_key
        ok = self._owner_key
        related = self._related

        fk_values = list({inst._attributes.get(fk) for inst in instances if inst._attributes.get(fk) is not None})
        if not fk_values:
            for inst in instances:
                inst._relations[rel_name] = None
            return

        qb = related.query().where_in(ok, fk_values)
        if constraint:
            qb = constraint(qb)
        results = qb.get()

        keyed = {r._attributes.get(ok): r for r in results}
        for inst in instances:
            inst._relations[rel_name] = keyed.get(inst._attributes.get(fk))
