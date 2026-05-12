from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ColumnDef:
    """Column definition with fluent modifier methods."""

    def __init__(self, name: str, type: str, **kwargs: Any) -> None:
        self.name = name
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

    # Fluent modifiers
    def nullable(self) -> "ColumnDef":
        self.is_nullable = True
        return self

    def not_nullable(self) -> "ColumnDef":
        self.is_nullable = False
        return self

    def default(self, value: Any) -> "ColumnDef":
        self.default_value = value
        return self

    def unique(self) -> "ColumnDef":
        self.is_unique = True
        return self

    def unsigned_modifier(self) -> "ColumnDef":
        self.unsigned = True
        return self

    def references(self, column: str) -> "ColumnDef":
        self.references_col = column
        return self

    def on(self, table: str) -> "ColumnDef":
        self.on_table = table
        return self


@dataclass
class IndexDef:
    columns: list[str]
    unique: bool = False
    name: str = ""


class Blueprint:
    def __init__(self, table: str) -> None:
        self.table = table
        self.columns: list[ColumnDef] = []
        self.indexes: list[IndexDef] = []
        self._drop_columns: list[str] = []

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
    # Foreign keys
    # ------------------------------------------------------------------

    def foreign_id(self, name: str) -> ColumnDef:
        col = ColumnDef(name, "INTEGER", unsigned=True)
        self.columns.append(col)
        return col

    def foreign(self, column: str) -> "_ForeignKeyBuilder":
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
    # Drop helpers (for alter)
    # ------------------------------------------------------------------

    def drop_column(self, *names: str) -> None:
        self._drop_columns.extend(names)

    # ------------------------------------------------------------------
    # SQL generation
    # ------------------------------------------------------------------

    def to_create_sql(self) -> list[str]:
        stmts = []
        col_defs = []
        for col in self.columns:
            col_defs.append(self._column_sql(col))
        create = f"CREATE TABLE IF NOT EXISTS {self.table} (\n  " + ",\n  ".join(col_defs) + "\n)"
        stmts.append(create)
        for idx in self.indexes:
            if idx.unique:
                stmts.append(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {idx.name} ON {self.table} ({', '.join(idx.columns)})"
                )
            else:
                stmts.append(
                    f"CREATE INDEX IF NOT EXISTS {idx.name} ON {self.table} ({', '.join(idx.columns)})"
                )
        return stmts

    @staticmethod
    def _column_sql(col: ColumnDef) -> str:
        sql = f"{col.name} {col.type}"
        if col.length:
            sql = f"{col.name} {col.type}({col.length})"
        if col.primary:
            sql += " PRIMARY KEY"
        if col.auto_increment:
            sql += " AUTOINCREMENT"
        if not col.is_nullable and not col.primary:
            sql += " NOT NULL"
        if col.default_value is not None:
            default_val = f"'{col.default_value}'" if isinstance(col.default_value, str) else str(col.default_value)
            sql += f" DEFAULT {default_val}"
        if col.is_unique and not col.primary:
            sql += " UNIQUE"
        return sql


class _ForeignKeyBuilder:
    def __init__(self, blueprint: Blueprint, column: str) -> None:
        self._blueprint = blueprint
        self._column = column

    def references(self, column: str) -> "_ForeignKeyBuilder":
        self._ref_column = column
        return self

    def on(self, table: str) -> "_ForeignKeyBuilder":
        self._ref_table = table
        return self
