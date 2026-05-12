from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.database.model import Model


class HasMany:
    def __init__(self, related: type, parent: "Model", foreign_key: str, local_key: str) -> None:
        self._related = related
        self._parent = parent
        self._foreign_key = foreign_key
        self._local_key = local_key

    def get_results(self) -> list:
        local_val = self._parent._attributes.get(self._local_key)
        return self._related.query().where(self._foreign_key, local_val).get()
