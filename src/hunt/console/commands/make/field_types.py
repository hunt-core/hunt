from __future__ import annotations

import click

from hunt.console.commands.make._naming import validate_identifier

_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "str": "string",
    "text": "text",
    "int": "integer",
    "integer": "integer",
    "bigint": "big_integer",
    "smallint": "small_integer",
    "float": "float",
    "decimal": "decimal",
    "bool": "boolean",
    "boolean": "boolean",
    "timestamp": "timestamp",
    "date": "date",
    "json": "json",
    "uuid": "uuid",
}

# Columns every generated migration already creates.
_RESERVED_COLUMNS = frozenset({"id", "created_at", "updated_at"})

# Blueprint method -> validation rules used by generated controllers.
_RULE_MAP: dict[str, str] = {
    "string": "required|string|max:255",
    "text": "required|string",
    "integer": "required|integer",
    "big_integer": "required|integer",
    "small_integer": "required|integer",
    "float": "required|numeric",
    "decimal": "required|numeric",
    "boolean": "boolean",
    "timestamp": "required",
    "date": "required|date",
    "json": "required",
    "uuid": "required|uuid",
}


def parse_fields(fields_str: str) -> list[tuple[str, str]]:
    """Parse 'name:type name:type ...' into a list of (name, blueprint_method) pairs."""
    if not fields_str:
        return []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for part in fields_str.split():
        if ":" in part:
            col, raw_type = part.split(":", 1)
        else:
            col, raw_type = part, "string"
        col = col.strip()
        # Column names are interpolated into generated Python and HTML.
        validate_identifier(col, "field name")
        if col in _RESERVED_COLUMNS:
            raise click.BadParameter(f"{col!r} is added automatically and cannot be used as a field name.")
        if col in seen:
            raise click.BadParameter(f"Field {col!r} is declared more than once.")
        seen.add(col)
        method = _TYPE_MAP.get(raw_type.lower(), "string")
        result.append((col, method))
    return result


def migration_columns(fields: list[tuple[str, str]]) -> str:
    """Return Blueprint column lines for a migration up() body."""
    lines = []
    for col, method in fields:
        lines.append(f'            bp.{method}("{col}"),')
    return "\n".join(lines)


def fillable_list(fields: list[tuple[str, str]]) -> str:
    """Return a Python list literal of field names for Model.fillable."""
    names = ", ".join(f'"{col}"' for col, _ in fields)
    return f"[{names}]"


def validation_rules(fields: list[tuple[str, str]]) -> dict[str, str]:
    """Return ``{column: "rule|rule"}`` for generated controllers and form requests."""
    return {col: _RULE_MAP.get(method, "required") for col, method in fields}


def rules_literal(fields: list[tuple[str, str]], indent: str = "    ") -> str:
    """Return a Python dict literal body (one ``"col": "rules",`` line per field)."""
    return "\n".join(f'{indent}"{col}": "{rule}",' for col, rule in validation_rules(fields).items())


def model_source(class_name: str, table: str, fields: list[tuple[str, str]], stub: str) -> str:
    """Render a model stub.

    ``fillable`` is always an explicit list of the declared fields. Models are
    guarded by default, so a model generated without ``--fields`` accepts no
    mass assignment until the developer lists its columns.
    """
    content = stub.replace("{{class}}", class_name).replace("{{table}}", table)
    content = content.replace("{{fillable}}", fillable_list(fields))
    if not fields:
        content = content.replace(
            "    fillable: list[str] = []\n",
            "    # No --fields were given: nothing is mass assignable. List the columns that\n"
            "    # create()/fill() may set from user input (never is_admin, owner ids, ...).\n"
            "    fillable: list[str] = []\n"
            '    guarded: list[str] = ["*"]\n',
            1,
        )
    return content
