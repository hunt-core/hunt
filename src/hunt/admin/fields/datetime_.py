from __future__ import annotations

import datetime
from typing import Any

from hunt.admin.field import Field


def _parse_and_format(value: Any, fmt: str) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime(fmt)
    if isinstance(value, (int, float)):
        # Unix timestamp
        try:
            return datetime.datetime.fromtimestamp(value).strftime(fmt)
        except (OSError, OverflowError, ValueError):
            return str(value)
    if isinstance(value, str):
        # Try common ISO formats
        for parse_fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(value, parse_fmt).strftime(fmt)
            except ValueError:
                continue
        return value
    return str(value)


class DateTime(Field):
    field_type: str = "datetime"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._format: str = "%Y-%m-%d %H:%M"

    def format(self, fmt: str) -> DateTime:
        self._format = fmt
        return self

    def display_value(self, instance: Any) -> str:
        return _parse_and_format(self.value_for(instance), self._format)


class Date(Field):
    field_type: str = "date"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._format: str = "%Y-%m-%d"

    def format(self, fmt: str) -> Date:
        self._format = fmt
        return self

    def display_value(self, instance: Any) -> str:
        return _parse_and_format(self.value_for(instance), self._format)
