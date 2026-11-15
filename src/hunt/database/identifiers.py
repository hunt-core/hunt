"""SQL identifier allowlists shared by the query builder, schema builder and relations."""

from __future__ import annotations

import re

# A bare identifier: table, column, index or constraint name.
_SIMPLE_IDENT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
# An identifier with optional ``table.column`` qualification.
_QUALIFIED_IDENT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?")


def simple_ident(name: str) -> str:
    """Validate and return a bare SQL identifier (no qualification)."""
    if not isinstance(name, str) or not _SIMPLE_IDENT_RE.fullmatch(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def qualified_ident(name: str) -> str:
    """Validate and return an identifier with optional ``table.column`` qualification (or ``*``)."""
    if name == "*":
        return name
    if not isinstance(name, str) or not _QUALIFIED_IDENT_RE.fullmatch(name):
        raise ValueError(
            f"Invalid SQL identifier {name!r}. Only alphanumeric characters and "
            "underscores are allowed, with optional table.column qualification."
        )
    return name
