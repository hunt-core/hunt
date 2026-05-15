from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.database.model import Model


class HasOne:
    def __init__(self, related: type, parent: Model, foreign_key: str, local_key: str) -> None:
        self._related = related
        self._parent = parent
        self._foreign_key = foreign_key
        self._local_key = local_key

    def get_result(self) -> Any:
        local_val = self._parent._attributes.get(self._local_key)
        return self._related.query().where(self._foreign_key, local_val).first()

    def eager_load_for(
        self,
        rel_name: str,
        instances: list[Model],
        constraint: Any = None,
    ) -> None:
        lk = self._local_key
        fk = self._foreign_key
        related = self._related

        parent_keys = list({inst._attributes.get(lk) for inst in instances if inst._attributes.get(lk) is not None})
        if not parent_keys:
            for inst in instances:
                inst._relations[rel_name] = None
            return

        qb = related.query().where_in(fk, parent_keys)
        if constraint:
            qb = constraint(qb)
        results = qb.get()

        keyed = {r._attributes.get(fk): r for r in reversed(results)}
        for inst in instances:
            inst._relations[rel_name] = keyed.get(inst._attributes.get(lk))
