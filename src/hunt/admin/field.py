from __future__ import annotations

from typing import Any

from hunt.support.str import Str


class Field:
    """Base field for AdminResource definitions."""

    field_type: str = "text"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        self.name = name
        self.attribute = attribute if attribute is not None else Str.snake(name)
        self.label = name
        self.field_type = self.__class__.field_type

        self._show_on_index: bool = True
        self._show_on_detail: bool = True
        self._show_on_create: bool = True
        self._show_on_edit: bool = True
        self._sortable: bool = False
        self._readonly: bool = False
        self._help_text: str = ""
        self._nullable: bool = False
        self._rules: list[str] = []
        self._panel: str | None = None

    # ------------------------------------------------------------------
    # Fluent visibility / behaviour setters
    # ------------------------------------------------------------------

    def hide_from_index(self) -> Field:
        self._show_on_index = False
        return self

    def hide_from_detail(self) -> Field:
        self._show_on_detail = False
        return self

    def hide_from_forms(self) -> Field:
        self._show_on_create = False
        self._show_on_edit = False
        return self

    def hide_from_create(self) -> Field:
        self._show_on_create = False
        return self

    def hide_from_edit(self) -> Field:
        self._show_on_edit = False
        return self

    def only_on_index(self) -> Field:
        self._show_on_detail = False
        self._show_on_create = False
        self._show_on_edit = False
        return self

    def only_on_forms(self) -> Field:
        self._show_on_index = False
        self._show_on_detail = False
        return self

    def only_on_detail(self) -> Field:
        self._show_on_index = False
        self._show_on_create = False
        self._show_on_edit = False
        return self

    def show_on_index(self) -> Field:
        self._show_on_index = True
        return self

    def show_on_detail(self) -> Field:
        self._show_on_detail = True
        return self

    def show_on_create(self) -> Field:
        self._show_on_create = True
        return self

    def show_on_edit(self) -> Field:
        self._show_on_edit = True
        return self

    def sortable(self) -> Field:
        self._sortable = True
        return self

    def readonly(self) -> Field:
        self._readonly = True
        return self

    def help(self, text: str) -> Field:
        self._help_text = text
        return self

    def rules(self, *r: str) -> Field:
        self._rules = list(r)
        return self

    def nullable(self) -> Field:
        self._nullable = True
        return self

    def panel(self, name: str) -> Field:
        self._panel = name
        return self

    # ------------------------------------------------------------------
    # Value resolution
    # ------------------------------------------------------------------

    def value_for(self, instance: Any) -> Any:
        return instance._attributes.get(self.attribute)

    def display_value(self, instance: Any) -> str:
        val = self.value_for(instance)
        if val is None:
            return ""
        return str(val)
