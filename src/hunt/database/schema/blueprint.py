from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hunt.database.identifiers import simple_ident as _ident

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


class ColumnDef:
    """Column definition with fluent modifier methods."""

    def __init__(self, name: str, type: str, **kwargs: Any) -> None:
        self.name = _ident(name)
        self.type = type
        self.is_nullable: bool = kwargs.get("nullable", False)
        self.default_value: Any = kwargs.get("default", None)
        self.primary: bool = kwargs.get("primary", False)
        self.is_unique: bool = kwargs.get("unique", False)
        self.is_unsigned: bool = kwargs.get("unsigned", False)
        self.auto_increment: bool = kwargs.get("auto_increment", False)
        self.length: int | None = kwargs.get("length", None)
        self.references_col: str | None = None
        self.on_table: str | None = None
        self.fk_on_delete: str | None = None
        self.fk_on_update: str | None = None
        self.enum_values: list | None = None
        # "id": auto-increment key. "foreign_id": a column sized to reference one.
        self.key_kind: str | None = kwargs.get("key_kind", None)
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

    def unsigned(self) -> ColumnDef:
        self.is_unsigned = True
        return self

    def references(self, column: str) -> ColumnDef:
        self.references_col = column
        return self

    def on(self, table: str) -> ColumnDef:
        self.on_table = table
        return self

    def on_delete(self, action: str) -> ColumnDef:
        """Referential action for a ``.references(...).on(...)`` foreign key."""
        self.fk_on_delete = _fk_action(action)
        return self

    def on_update(self, action: str) -> ColumnDef:
        self.fk_on_update = _fk_action(action)
        return self

    def foreign_key(self) -> ForeignKeyDef | None:
        """The foreign key declared with ``.references(col).on(table)``, if any."""
        if not self.on_table:
            return None
        return ForeignKeyDef(
            column=self.name,
            ref_table=self.on_table,
            ref_column=self.references_col or "id",
            on_delete=self.fk_on_delete,
            on_update=self.fk_on_update,
        )

    def change(self) -> ColumnDef:
        """Mark this column as a modification to an existing column (ALTER)."""
        self.is_change = True
        return self

    def after(self, column: str) -> ColumnDef:
        """MySQL AFTER hint — no-op on SQLite/PostgreSQL."""
        self.after_column = _ident(column)
        return self

    def primary_key(self) -> ColumnDef:
        """Mark this column as the table's PRIMARY KEY (for non-integer PKs)."""
        self.primary = True
        return self


_VALID_FK_ACTIONS = frozenset({"CASCADE", "SET NULL", "RESTRICT", "NO ACTION", "SET DEFAULT"})


def _fk_action(action: str) -> str:
    action_upper = str(action).upper()
    if action_upper not in _VALID_FK_ACTIONS:
        raise ValueError(f"Invalid FK action: {action!r}. Must be one of: {', '.join(sorted(_VALID_FK_ACTIONS))}")
    return action_upper


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
    name: str = ""

    def constraint_name(self, table: str) -> str:
        return self.name or f"{table}_{self.column}_foreign"

    def to_sql(self, table: str) -> str:
        """``CONSTRAINT <name> FOREIGN KEY (...) REFERENCES ...`` for CREATE / ALTER TABLE."""
        sql = (
            f"CONSTRAINT {_ident(self.constraint_name(table))} "
            f"FOREIGN KEY ({_ident(self.column)}) REFERENCES {_ident(self.ref_table)}({_ident(self.ref_column)})"
        )
        if self.on_delete:
            sql += f" ON DELETE {_fk_action(self.on_delete)}"
        if self.on_update:
            sql += f" ON UPDATE {_fk_action(self.on_update)}"
        return sql


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
        self._primary_columns: list[str] = []
        self._drop_indexes: list[str] = []
        self._drop_foreign_keys: list[str] = []

    # ------------------------------------------------------------------
    # Integer types
    # ------------------------------------------------------------------

    def id(self, name: str = "id") -> ColumnDef:
        # BIGINT on MySQL / PostgreSQL (see _key_type); SQLite needs the exact
        # spelling INTEGER PRIMARY KEY for its rowid alias.
        col = ColumnDef(name, "INTEGER", primary=True, auto_increment=True, key_kind="id")
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

    def uuid(self, name: str) -> ColumnDef:
        # CHAR(36) stores the canonical hyphenated form and is accepted by every
        # supported dialect; Postgres' native UUID type is intentionally avoided
        # to keep the schema portable.
        col = ColumnDef(name, "CHAR", length=36)
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
        # Unix seconds. BIGINT, not a 32-bit INTEGER that overflows in 2038.
        col = ColumnDef(name, "BIGINT")
        self.columns.append(col)
        return col

    def timestamps(self) -> None:
        self.timestamp("created_at").nullable()
        self.timestamp("updated_at").nullable()

    def soft_deletes(self, column: str = "deleted_at") -> ColumnDef:
        col = ColumnDef(column, "BIGINT", nullable=True)
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
        # Same type as id() on every dialect, so a FOREIGN KEY between the two
        # is accepted (MySQL rejects signed/unsigned or INT/BIGINT mismatches).
        col = ColumnDef(name, "INTEGER", unsigned=True, key_kind="foreign_id")
        self.columns.append(col)
        return col

    def foreign(self, column: str) -> _ForeignKeyBuilder:
        return _ForeignKeyBuilder(self, column)

    # ------------------------------------------------------------------
    # Indexes
    # ------------------------------------------------------------------

    def primary(self, columns: str | list[str]) -> None:
        """Declare a (composite) PRIMARY KEY."""
        if isinstance(columns, str):
            columns = [columns]
        self._primary_columns = [_ident(c) for c in columns]

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

    def drop_index(self, name_or_columns: str | list[str]) -> None:
        """Drop an index by name, or by its column list (``index([...])`` naming)."""
        if isinstance(name_or_columns, str):
            self._drop_indexes.append(_ident(name_or_columns))
        else:
            self._drop_indexes.append(_ident(f"{self.table}_{'_'.join(name_or_columns)}_index"))

    def drop_unique(self, name_or_columns: str | list[str]) -> None:
        """Drop a unique index by name, or by its column list (``unique([...])`` naming)."""
        if isinstance(name_or_columns, str):
            self._drop_indexes.append(_ident(name_or_columns))
        else:
            self._drop_indexes.append(_ident(f"{self.table}_{'_'.join(name_or_columns)}_unique"))

    def drop_foreign(self, name_or_column: str | list[str]) -> None:
        """Drop a foreign key by constraint name, or ``[column]`` for the default name."""
        if isinstance(name_or_column, str):
            self._drop_foreign_keys.append(_ident(name_or_column))
        else:
            self._drop_foreign_keys.append(_ident(f"{self.table}_{'_'.join(name_or_column)}_foreign"))

    def foreign_key_defs(self) -> list[ForeignKeyDef]:
        """Every foreign key this blueprint declares: ``foreign(...)`` builders
        and ``column.references(...).on(...)`` on its columns."""
        fks = [fk for fk in self._foreign_keys if fk.ref_table]
        declared = {fk.column for fk in fks}
        for col in self.columns:
            fk = col.foreign_key()
            if fk is not None and fk.column not in declared:
                fks.append(fk)
                declared.add(fk.column)
        return fks

    def index_sql(self, dialect: str = "sqlite", if_not_exists: bool = True) -> list[str]:
        """CREATE [UNIQUE] INDEX statements for the declared indexes."""
        t = _ident(self.table)
        # MySQL does not support IF NOT EXISTS for index creation.
        ine = "IF NOT EXISTS " if if_not_exists and dialect != "mysql" else ""
        stmts = []
        for idx in self.indexes:
            idx_name = _ident(idx.name) if idx.name else f"{t}_idx"
            safe_cols = ", ".join(_ident(c) for c in idx.columns)
            unique = "UNIQUE " if idx.unique else ""
            stmts.append(f"CREATE {unique}INDEX {ine}{idx_name} ON {t} ({safe_cols})")
        return stmts

    # ------------------------------------------------------------------
    # SQL generation
    # ------------------------------------------------------------------

    def to_create_sql(self, dialect: str = "sqlite") -> list[str]:
        stmts = []
        col_defs = []
        for col in self.columns:
            col_defs.append(self._column_sql(col, dialect))
        t = _ident(self.table)
        if self._primary_columns:
            col_defs.append(f"PRIMARY KEY ({', '.join(_ident(c) for c in self._primary_columns)})")
        for fk in self.foreign_key_defs():
            col_defs.append(fk.to_sql(t))
        create = f"CREATE TABLE IF NOT EXISTS {t} (\n  " + ",\n  ".join(col_defs) + "\n)"
        stmts.append(create)
        stmts.extend(self.index_sql(dialect))
        return stmts

    @staticmethod
    def _resolve_type(col_type: str, dialect: str) -> str:
        """Map a column type string to the correct spelling for the target dialect."""
        if dialect == "postgresql":
            mapped = _PG_TYPE_MAP.get(col_type)
            if mapped:
                return mapped
            m = _FLOAT_ARGS_RE.fullmatch(col_type)
            if m:
                return "REAL" if int(m.group(1)) <= 24 else "DOUBLE PRECISION"
        return col_type

    @staticmethod
    def _key_type(col: ColumnDef, dialect: str) -> tuple[str, bool] | None:
        """(type, unsigned) for id() / foreign_id() columns, or None for ordinary columns.

        Both are BIGINT UNSIGNED on MySQL and BIGINT on PostgreSQL so they can
        reference each other; SQLite keeps INTEGER (required for AUTOINCREMENT).
        """
        if col.key_kind not in ("id", "foreign_id") or col.type != "INTEGER":
            return None
        if dialect == "mysql":
            return "BIGINT", True
        if dialect == "postgresql":
            return "BIGINT", False
        return "INTEGER", False

    @staticmethod
    def _type_sql(col: ColumnDef, dialect: str = "sqlite") -> str:
        """The column's type for *dialect*, including length and UNSIGNED."""
        key_type = Blueprint._key_type(col, dialect)
        if key_type is not None:
            col_type, unsigned = key_type
        else:
            col_type, unsigned = Blueprint._resolve_type(col.type, dialect), col.is_unsigned
        sql = f"{col_type}({int(col.length)})" if col.length else col_type
        if unsigned and dialect == "mysql":
            sql += " UNSIGNED"
        return sql

    @staticmethod
    def _column_sql(col: ColumnDef, dialect: str = "sqlite") -> str:
        sql = f"{col.name} {Blueprint._type_sql(col, dialect)}"
        if col.primary:
            sql += " PRIMARY KEY"
        if col.auto_increment:
            if dialect == "mysql":
                sql += " AUTO_INCREMENT"
            elif dialect == "postgresql":
                # BY DEFAULT (not ALWAYS): explicit ids in seeds, imports and
                # restores are accepted; omitted ids are still generated.
                sql += " GENERATED BY DEFAULT AS IDENTITY"
            else:
                sql += " AUTOINCREMENT"
        if not col.is_nullable and not col.primary:
            sql += " NOT NULL"
        if col.default_value is not None:
            from hunt.database.schema.builder import _safe_default

            sql += f" DEFAULT {_safe_default(col.default_value, dialect, col.type)}"
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
        self._def.on_delete = _fk_action(action)
        return self

    def on_update(self, action: str) -> _ForeignKeyBuilder:
        self._def.on_update = _fk_action(action)
        return self

    def name(self, name: str) -> _ForeignKeyBuilder:
        """Set the constraint name (default: ``<table>_<column>_foreign``)."""
        self._def.name = _ident(name)
        return self
