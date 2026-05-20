from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Types that need renaming on PostgreSQL (which rejects MySQL-specific names).
_PG_TYPE_MAP: dict[str, str] = {
    "TINYINT": "SMALLINT",
    "MEDIUMTEXT": "TEXT",
    "LONGTEXT": "TEXT",
    "DATETIME": "TIMESTAMP",
    "DOUBLE": "DOUBLE PRECISION",
    "BLOB": "BYTEA",
}

# Matches FLOAT(precision,scale) as produced by Blueprint.float().
_FLOAT_ARGS_RE = re.compile(r"^FLOAT\((\d+),\d+\)$")


def _ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


class ColumnDef:
    """Column definition with fluent modifier methods."""

    def __init__(self, name: str, type: str, **kwargs: Any) -> None:
        self.name = _ident(name)
        self.type = type
        self.is_nullable: bool = kwargs.get("nullable", False)
        self.default_value: Any = kwargs.get("default", None)
        self.primary: bool = kwargs.get("primary", False)
        self.is_unique: bool = kwargs.get("unique", False)
        self.unsigned: bool = kwargs.get("unsigned", False)
        self.auto_increment: bool = kwargs.get("auto_increment", False)
        self.length: int | None = kwargs.get("length", None)
        self.references_col: str | None = None
        self.on_table: str | None = None
        self.enum_values: list | None = None
        # Modification flags
        self.is_change: bool = False  # ALTER COLUMN instead of ADD COLUMN
        self.after_column: str | None = None  # MySQL AFTER hint (no-op on SQLite)

    # Fluent modifiers
    def nullable(self) -> ColumnDef:
        self.is_nullable = True
        return self

    def not_nullable(self) -> ColumnDef:
        self.is_nullable = False
        return self

    def default(self, value: Any) -> ColumnDef:
        self.default_value = value
        return self

    def unique(self) -> ColumnDef:
        self.is_unique = True
        return self

    def unsigned_modifier(self) -> ColumnDef:
        self.unsigned = True
        return self

    def references(self, column: str) -> ColumnDef:
        self.references_col = column
        return self

    def on(self, table: str) -> ColumnDef:
        self.on_table = table
        return self

    def change(self) -> ColumnDef:
        """Mark this column as a modification to an existing column (ALTER)."""
        self.is_change = True
        return self

    def after(self, column: str) -> ColumnDef:
        """MySQL AFTER hint — no-op on SQLite/PostgreSQL."""
        self.after_column = column
        return self


_VALID_FK_ACTIONS = frozenset({"CASCADE", "SET NULL", "RESTRICT", "NO ACTION", "SET DEFAULT"})


@dataclass
class IndexDef:
    columns: list[str]
    unique: bool = False
    name: str = ""


@dataclass
class ForeignKeyDef:
    column: str
    ref_table: str = ""
    ref_column: str = "id"
    on_delete: str | None = None
    on_update: str | None = None


class Blueprint:
    def __init__(self, table: str) -> None:
        self.table = table
        self.columns: list[ColumnDef] = []
        self.indexes: list[IndexDef] = []
        self._foreign_keys: list[ForeignKeyDef] = []
        self._drop_columns: list[str] = []
        self._drop_columns_if_exists: list[str] = []
        self._rename_columns: list[tuple[str, str]] = []  # [(old, new), ...]
        self._rename_to: str | None = None

    # ------------------------------------------------------------------
    # Integer types
    # ------------------------------------------------------------------

    def id(self, name: str = "id") -> ColumnDef:
        col = ColumnDef(name, "INTEGER", primary=True, auto_increment=True)
        self.columns.append(col)
        return col

    def integer(self, name: str, unsigned: bool = False) -> ColumnDef:
        col = ColumnDef(name, "INTEGER", unsigned=unsigned)
        self.columns.append(col)
        return col

    def big_integer(self, name: str, unsigned: bool = False) -> ColumnDef:
        col = ColumnDef(name, "BIGINT", unsigned=unsigned)
        self.columns.append(col)
        return col

    def small_integer(self, name: str, unsigned: bool = False) -> ColumnDef:
        col = ColumnDef(name, "SMALLINT", unsigned=unsigned)
        self.columns.append(col)
        return col

    def tiny_integer(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "TINYINT")
        self.columns.append(col)
        return col

    def unsigned_integer(self, name: str) -> ColumnDef:
        return self.integer(name, unsigned=True)

    def unsigned_big_integer(self, name: str) -> ColumnDef:
        return self.big_integer(name, unsigned=True)

    # ------------------------------------------------------------------
    # String / text
    # ------------------------------------------------------------------

    def string(self, name: str, length: int = 255) -> ColumnDef:
        col = ColumnDef(name, "VARCHAR", length=length)
        self.columns.append(col)
        return col

    def char(self, name: str, length: int = 1) -> ColumnDef:
        col = ColumnDef(name, "CHAR", length=length)
        self.columns.append(col)
        return col

    def text(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "TEXT")
        self.columns.append(col)
        return col

    def long_text(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "LONGTEXT")
        self.columns.append(col)
        return col

    def medium_text(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "MEDIUMTEXT")
        self.columns.append(col)
        return col

    # ------------------------------------------------------------------
    # Enum
    # ------------------------------------------------------------------

    def enum(self, name: str, values: list[str]) -> ColumnDef:
        """VARCHAR column with a CHECK constraint restricting allowed values."""
        col = ColumnDef(name, "VARCHAR", length=255)
        col.enum_values = list(values)
        self.columns.append(col)
        return col

    # ------------------------------------------------------------------
    # Numeric
    # ------------------------------------------------------------------

    def float(self, name: str, precision: int = 8, scale: int = 2) -> ColumnDef:
        col = ColumnDef(name, f"FLOAT({precision},{scale})")
        self.columns.append(col)
        return col

    def double(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "DOUBLE")
        self.columns.append(col)
        return col

    def decimal(self, name: str, precision: int = 8, scale: int = 2) -> ColumnDef:
        col = ColumnDef(name, f"DECIMAL({precision},{scale})")
        self.columns.append(col)
        return col

    # ------------------------------------------------------------------
    # Boolean / binary
    # ------------------------------------------------------------------

    def boolean(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "BOOLEAN")
        self.columns.append(col)
        return col

    def binary(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "BLOB")
        self.columns.append(col)
        return col

    # ------------------------------------------------------------------
    # Date / time
    # ------------------------------------------------------------------

    def date(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "DATE")
        self.columns.append(col)
        return col

    def datetime(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "DATETIME")
        self.columns.append(col)
        return col

    def timestamp(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "INTEGER")
        self.columns.append(col)
        return col

    def timestamps(self) -> None:
        self.timestamp("created_at").nullable()
        self.timestamp("updated_at").nullable()

    def soft_deletes(self, column: str = "deleted_at") -> ColumnDef:
        col = ColumnDef(column, "INTEGER", nullable=True)
        self.columns.append(col)
        return col

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def json(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "TEXT")
        self.columns.append(col)
        return col

    def json_b(self, name: str) -> ColumnDef:
        return self.json(name)

    # ------------------------------------------------------------------
    # Polymorphic morphs
    # ------------------------------------------------------------------

    def morphs(self, name: str) -> None:
        """Add {name}_id (BIGINT) and {name}_type (VARCHAR) with a composite index."""
        self.unsigned_big_integer(f"{name}_id")
        self.string(f"{name}_type")
        self.index([f"{name}_id", f"{name}_type"], name=f"{self.table}_{name}_index")

    def nullable_morphs(self, name: str) -> None:
        """Add nullable {name}_id and {name}_type columns with a composite index."""
        self.unsigned_big_integer(f"{name}_id").nullable()
        self.string(f"{name}_type").nullable()
        self.index([f"{name}_id", f"{name}_type"], name=f"{self.table}_{name}_index")

    # ------------------------------------------------------------------
    # Foreign keys
    # ------------------------------------------------------------------

    def foreign_id(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "INTEGER", unsigned=True)
        self.columns.append(col)
        return col

    def foreign(self, column: str) -> _ForeignKeyBuilder:
        return _ForeignKeyBuilder(self, column)

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    def primary(self, columns: list[str]) -> None:
        self.indexes.append(IndexDef(columns, unique=True, name=f"{self.table}_primary"))

    def unique(self, columns: str | list[str]) -> None:
        if isinstance(columns, str):
            columns = [columns]
        self.indexes.append(IndexDef(columns, unique=True, name=f"{self.table}_{'_'.join(columns)}_unique"))

    def index(self, columns: str | list[str], name: str | None = None) -> None:
        if isinstance(columns, str):
            columns = [columns]
        self.indexes.append(IndexDef(columns, unique=False, name=name or f"{self.table}_{'_'.join(columns)}_index"))

    # ------------------------------------------------------------------
    # Rename / alter helpers
    # ------------------------------------------------------------------

    def rename_column(self, from_name: str, to_name: str) -> None:
        """Queue a column rename to be applied by Schema.table()."""
        self._rename_columns.append((from_name, to_name))

    def rename_table(self, to: str) -> None:
        """Queue a table rename to be applied by Schema.table()."""
        self._rename_to = to

    # ------------------------------------------------------------------
    # Drop helpers (for alter)
    # ------------------------------------------------------------------

    def drop_column(self, *names: str) -> None:
        self._drop_columns.extend(names)

    def drop_column_if_exists(self, *names: str) -> None:
        self._drop_columns_if_exists.extend(names)

    # ------------------------------------------------------------------
    # SQL generation
    # ------------------------------------------------------------------

    def to_create_sql(self, dialect: str = "sqlite") -> list[str]:
        stmts = []
        col_defs = []
        for col in self.columns:
            col_defs.append(self._column_sql(col, dialect))
        for fk in self._foreign_keys:
            if not fk.ref_table:
                continue
            fk_sql = f"FOREIGN KEY ({_ident(fk.column)}) REFERENCES {_ident(fk.ref_table)}({_ident(fk.ref_column)})"
            if fk.on_delete:
                fk_sql += f" ON DELETE {fk.on_delete}"
            if fk.on_update:
                fk_sql += f" ON UPDATE {fk.on_update}"
            col_defs.append(fk_sql)
        t = _ident(self.table)
        create = f"CREATE TABLE IF NOT EXISTS {t} (\n  " + ",\n  ".join(col_defs) + "\n)"
        stmts.append(create)
        # MySQL does not support IF NOT EXISTS for index creation.
        if_not_exists = "" if dialect == "mysql" else "IF NOT EXISTS "
        for idx in self.indexes:
            idx_name = _ident(idx.name) if idx.name else f"{t}_idx"
            safe_cols = ", ".join(_ident(c) for c in idx.columns)
            if idx.unique:
                stmts.append(f"CREATE UNIQUE INDEX {if_not_exists}{idx_name} ON {t} ({safe_cols})")
            else:
                stmts.append(f"CREATE INDEX {if_not_exists}{idx_name} ON {t} ({safe_cols})")
        return stmts

    @staticmethod
    def _resolve_type(col_type: str, dialect: str) -> str:
        """Map a column type string to the correct spelling for the target dialect."""
        if dialect == "postgresql":
            mapped = _PG_TYPE_MAP.get(col_type)
            if mapped:
                return mapped
            m = _FLOAT_ARGS_RE.match(col_type)
            if m:
                return "REAL" if int(m.group(1)) <= 24 else "DOUBLE PRECISION"
        return col_type

    @staticmethod
    def _column_sql(col: ColumnDef, dialect: str = "sqlite") -> str:
        col_type = Blueprint._resolve_type(col.type, dialect)
        if col.length:
            sql = f"{col.name} {col_type}({col.length})"
        else:
            sql = f"{col.name} {col_type}"
        if col.unsigned and dialect == "mysql":
            sql += " UNSIGNED"
        if col.primary:
            sql += " PRIMARY KEY"
        if col.auto_increment:
            if dialect == "mysql":
                sql += " AUTO_INCREMENT"
            elif dialect == "postgresql":
                sql += " GENERATED ALWAYS AS IDENTITY"
            else:
                sql += " AUTOINCREMENT"
        if not col.is_nullable and not col.primary:
            sql += " NOT NULL"
        if col.default_value is not None:
            from hunt.database.schema.builder import _safe_default

            sql += f" DEFAULT {_safe_default(col.default_value, dialect)}"
        if col.is_unique and not col.primary:
            sql += " UNIQUE"
        if col.enum_values:
            quoted = ", ".join(f"'{v.replace(chr(39), chr(39) + chr(39))}'" for v in col.enum_values)
            sql += f" CHECK({col.name} IN ({quoted}))"
        return sql


class _ForeignKeyBuilder:
    def __init__(self, blueprint: Blueprint, column: str) -> None:
        self._def = ForeignKeyDef(column=column)
        blueprint._foreign_keys.append(self._def)

    def references(self, column: str) -> _ForeignKeyBuilder:
        self._def.ref_column = column
        return self

    def on(self, table: str) -> _ForeignKeyBuilder:
        self._def.ref_table = table
        return self

    def on_delete(self, action: str) -> _ForeignKeyBuilder:
        action_upper = action.upper()
        if action_upper not in _VALID_FK_ACTIONS:
            raise ValueError(f"Invalid FK action: {action!r}. Must be one of: {', '.join(sorted(_VALID_FK_ACTIONS))}")
        self._def.on_delete = action_upper
        return self

    def on_update(self, action: str) -> _ForeignKeyBuilder:
        action_upper = action.upper()
        if action_upper not in _VALID_FK_ACTIONS:
            raise ValueError(f"Invalid FK action: {action!r}. Must be one of: {', '.join(sorted(_VALID_FK_ACTIONS))}")
        self._def.on_update = action_upper
        return self
