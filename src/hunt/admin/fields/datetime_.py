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


_PARSE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _to_datetime(value: Any) -> datetime.datetime | None:
    """Best-effort conversion of a stored or submitted value to a naive local datetime."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(value)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            try:
                return datetime.datetime.fromtimestamp(int(text))
            except (OSError, OverflowError, ValueError):
                return None
        for parse_fmt in _PARSE_FORMATS:
            try:
                return datetime.datetime.strptime(text, parse_fmt)
            except ValueError:
                continue
    return None


class _TemporalField(Field):
    """Shared form <-> storage conversion for DateTime and Date."""

    _form_format: str = "%Y-%m-%dT%H:%M"
    _string_format: str = "%Y-%m-%d %H:%M:%S"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        # "auto": keep the type the column already holds (epoch stays epoch,
        # datetime stays datetime), otherwise store a string.
        self._storage: str = "auto"

    def as_timestamp(self) -> Any:
        """Store the value as a Unix timestamp (the framework's ``created_at`` format)."""
        self._storage = "timestamp"
        return self

    def as_string(self, fmt: str | None = None) -> Any:
        """Store the value as a formatted string (default ``YYYY-MM-DD HH:MM:SS`` / ``YYYY-MM-DD``)."""
        self._storage = "string"
        if fmt:
            self._string_format = fmt
        return self

    def to_form_value(self, raw: Any) -> Any:
        if raw is None or raw == "":
            return ""
        dt = _to_datetime(raw)
        if dt is None:
            return ""
        return dt.strftime(self._form_format)

    def from_form_value(self, value: Any, current: Any = None) -> Any:
        if value is None or value == "":
            return None
        dt = _to_datetime(value)
        if dt is None:
            return value
        storage = self._storage
        if storage == "auto":
            if isinstance(current, (int, float)) and not isinstance(current, bool):
                storage = "timestamp"
            elif isinstance(current, datetime.datetime):
                return dt
            elif isinstance(current, datetime.date):
                return dt.date()
            else:
                storage = "string"
        if storage == "timestamp":
            return int(dt.timestamp())
        return dt.strftime(self._string_format)


class DateTime(_TemporalField):
    field_type: str = "datetime"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._format: str = "%Y-%m-%d %H:%M"

    def format(self, fmt: str) -> DateTime:
        self._format = fmt
        return self

    def display_value(self, instance: Any) -> str:
        return _parse_and_format(self.value_for(instance), self._format)


class Date(_TemporalField):
    field_type: str = "date"
    _form_format: str = "%Y-%m-%d"
    _string_format: str = "%Y-%m-%d"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self._format: str = "%Y-%m-%d"

    def format(self, fmt: str) -> Date:
        self._format = fmt
        return self

    def display_value(self, instance: Any) -> str:
        return _parse_and_format(self.value_for(instance), self._format)
