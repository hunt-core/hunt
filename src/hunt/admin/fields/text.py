from __future__ import annotations

from hunt.admin.field import Field


class Text(Field):
    field_type: str = "text"


class Email(Field):
    field_type: str = "email"


class Password(Field):
    field_type: str = "password"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self.hide_from_index()
        self.hide_from_detail()


class Slug(Field):
    field_type: str = "slug"
