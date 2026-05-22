from __future__ import annotations

_TYPE_MAP: dict[str, str] = {
    "string":    "string",
    "str":       "string",
    "text":      "text",
    "int":       "integer",
    "integer":   "integer",
    "bigint":    "big_integer",
    "smallint":  "small_integer",
    "float":     "float",
    "decimal":   "decimal",
    "bool":      "boolean",
    "boolean":   "boolean",
    "timestamp": "timestamp",
    "date":      "date",
    "json":      "json",
    "uuid":      "uuid",
}


def parse_fields(fields_str: str) -> list[tuple[str, str]]:
    """Parse 'name:type name:type ...' into a list of (name, blueprint_method) pairs."""
    if not fields_str:
        return []
    result: list[tuple[str, str]] = []
    for part in fields_str.split():
        if ":" in part:
            col, raw_type = part.split(":", 1)
        else:
            col, raw_type = part, "string"
        method = _TYPE_MAP.get(raw_type.lower(), "string")
        result.append((col.strip(), method))
    return result


def migration_columns(fields: list[tuple[str, str]]) -> str:
    """Return Blueprint column lines for a migration up() body."""
    lines = []
    for col, method in fields:
        lines.append(f"            bp.{method}(\"{col}\"),")
    return "\n".join(lines)


def fillable_list(fields: list[tuple[str, str]]) -> str:
    """Return a Python list literal of field names for Model.fillable."""
    names = ", ".join(f'"{col}"' for col, _ in fields)
    return f"[{names}]"
