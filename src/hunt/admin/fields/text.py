from __future__ import annotations

from typing import Any

from hunt.admin.field import SKIP, Field


class Text(Field):
    field_type: str = "text"


class Email(Field):
    field_type: str = "email"


class Password(Field):
    field_type: str = "password"
    sensitive: bool = True
    keep_when_blank_on_update: bool = True

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self.hide_from_index()
        self.hide_from_detail()

    def value_for(self, instance: Any) -> Any:
        # The stored hash is never shown or echoed back into a form.
        return None

    def display_value(self, instance: Any) -> str:
        return ""

    def to_form_value(self, raw: Any) -> Any:
        return ""

    def from_form_value(self, value: Any, current: Any = None) -> Any:
        return value

    def prepare_for_storage(self, value: Any, *, is_update: bool) -> Any:
        """Hash the submitted password; a blank value leaves the column untouched."""
        if value is None or value == "":
            return SKIP
        from hunt.auth.manager import hash_password

        return hash_password(str(value))


class Slug(Field):
    field_type: str = "slug"
