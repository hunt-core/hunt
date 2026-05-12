from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.database.model import Model


class BelongsTo:
    def __init__(self, related: type, child: "Model", foreign_key: str, owner_key: str) -> None:
        self._related = related
        self._child = child
        self._foreign_key = foreign_key
        self._owner_key = owner_key

    def get_result(self) -> Any:
        fk_val = self._child._attributes.get(self._foreign_key)
        if fk_val is None:
            return None
        return self._related.query().where(self._owner_key, fk_val).first()
