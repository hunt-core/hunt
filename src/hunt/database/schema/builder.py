from __future__ import annotations

import re
from collections.abc import Callable

from sqlalchemy import text

from hunt.database.connection import connection
from hunt.database.schema.blueprint import Blueprint, ColumnDef

_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _ident(name: str) -> str:
    """Validate and return a safe SQL identifier (table/column/index name)."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def _coerce_bool(value: object) -> bool:
    """Interpret a non-bool default for a BOOLEAN column as a boolean."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(value)


def _safe_default(value: object, dialect: str = "sqlite", column_type: str | None = None) -> str:
    """Render a column default value as a safe SQL literal.

    When *column_type* is ``"BOOLEAN"``, a non-bool default (e.g. ``.default(0)``
    or ``.default("true")``) is coerced to a real boolean first — Postgres
    rejects ``DEFAULT 0`` on a boolean column, so the literal must match the type.
    """
    if column_type == "BOOLEAN" and not isinstance(value, bool):
        value = _coerce_bool(value)
    if isinstance(value, bool):
        if dialect == "postgresql":
            return "true" if value else "false"
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    raise TypeError(f"Unsupported default value type: {type(value).__name__!r}")


class Schema:
    _conn_name: str | None = None

    @classmethod
    def create(cls, table: str, callback: Callable[[Blueprint], None]) -> None:
        bp = Blueprint(_ident(table))
        callback(bp)
        engine = connection(cls._conn_name)
        dialect = engine.dialect.name
        with engine.connect() as conn:
            for sql in bp.to_create_sql(dialect):
                conn.execute(text(sql))
            conn.commit()

    @classmethod
    def table(cls, table: str, callback: Callable[[Blueprint], None]) -> None:
        """Alter an existing table: add/change columns, drop columns, rename columns/table."""
        t = _ident(table)
        bp = Blueprint(t)
        callback(bp)
        engine = connection(cls._conn_name)
        dialect = engine.dialect.name  # 'sqlite', 'mysql', 'postgresql'

        with engine.connect() as conn:
            # 1. Add new columns
            new_cols = [c for c in bp.columns if not c.is_change]
            for col in new_cols:
                sql = f"ALTER TABLE {t} ADD COLUMN {Blueprint._column_sql(col, dialect)}"
                conn.execute(text(sql))

            # 2. Alter existing columns
            change_cols = [c for c in bp.columns if c.is_change]
            if change_cols:
                if dialect == "sqlite":
                    _sqlite_rebuild_columns(conn, t, change_cols)
                elif dialect == "mysql":
                    for col in change_cols:
                        conn.execute(text(f"ALTER TABLE {t} MODIFY COLUMN {Blueprint._column_sql(col, dialect)}"))
                else:  # postgresql and others
                    for col in change_cols:
                        _pg_alter_column(conn, t, col)

            # 3. Drop columns
            for col_name in bp._drop_columns:
                conn.execute(text(f"ALTER TABLE {t} DROP COLUMN {_ident(col_name)}"))
            for col_name in bp._drop_columns_if_exists:
                _drop_column_if_exists(conn, t, col_name, dialect)

            # 4. Rename columns
            for old_name, new_name in bp._rename_columns:
                conn.execute(text(f"ALTER TABLE {t} RENAME COLUMN {_ident(old_name)} TO {_ident(new_name)}"))

            # 5. Rename table (must be last)
            if bp._rename_to:
                conn.execute(text(f"ALTER TABLE {t} RENAME TO {_ident(bp._rename_to)}"))

            conn.commit()

    @classmethod
    def drop(cls, table: str) -> None:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"DROP TABLE {_ident(table)}"))
            conn.commit()

    @classmethod
    def drop_if_exists(cls, table: str) -> None:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {_ident(table)}"))
            conn.commit()

    @classmethod
    def has_table(cls, table: str) -> bool:
        engine = connection(cls._conn_name)
        dialect = engine.dialect.name
        with engine.connect() as conn:
            if dialect == "postgresql":
                result = conn.execute(
                    text("SELECT 1 FROM information_schema.tables WHERE table_name=:t"),
                    {"t": table},
                )
            elif dialect == "mysql":
                result = conn.execute(
                    text("SELECT 1 FROM information_schema.tables WHERE table_name=:t AND table_schema=DATABASE()"),
                    {"t": table},
                )
            else:
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                    {"t": table},
                )
            return result.fetchone() is not None

    @classmethod
    def has_column(cls, table: str, column: str) -> bool:
        engine = connection(cls._conn_name)
        t = _ident(table)
        dialect = engine.dialect.name
        with engine.connect() as conn:
            if dialect == "postgresql":
                result = conn.execute(
                    text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"),
                    {"t": table, "c": column},
                )
                return result.fetchone() is not None
            elif dialect == "mysql":
                result = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_name=:t AND column_name=:c AND table_schema=DATABASE()"
                    ),
                    {"t": table, "c": column},
                )
                return result.fetchone() is not None
            else:
                result = conn.execute(text(f"PRAGMA table_info({t})"))
                return any(row[1] == column for row in result.fetchall())

    @classmethod
    def rename(cls, from_table: str, to_table: str) -> None:
        """Rename a table (convenience method — no Blueprint needed)."""
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {_ident(from_table)} RENAME TO {_ident(to_table)}"))
            conn.commit()

    @classmethod
    def rename_column(cls, table: str, from_col: str, to_col: str) -> None:
        """Rename a single column (convenience method — no Blueprint needed)."""
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {_ident(table)} RENAME COLUMN {_ident(from_col)} TO {_ident(to_col)}"))
            conn.commit()


# ---------------------------------------------------------------------------
# Driver-specific ALTER COLUMN helpers
# ---------------------------------------------------------------------------


def _sqlite_rebuild_columns(conn, table: str, changes: list[ColumnDef]) -> None:
    """
    SQLite does not support ALTER COLUMN; rebuild the table to apply changes.

    Strategy:
      1. Read current schema via PRAGMA table_info.
      2. Construct new CREATE TABLE with changed column definitions applied.
      3. Copy data into a temp table.
      4. Drop the original.
      5. Rename temp to original.
    """
    t = _ident(table)
    result = conn.execute(text(f"PRAGMA table_info({t})"))
    rows = result.fetchall()
    # Each row: (cid, name, type, notnull, dflt_value, pk)
    if not rows:
        raise RuntimeError(f"Table '{table}' not found or is empty (PRAGMA returned nothing).")

    col_names = [row[1] for row in rows]
    change_map = {c.name: c for c in changes}

    _SAFE_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_ ()]*$")

    new_col_defs: list[str] = []
    for row in rows:
        name = row[1]
        if name in change_map:
            new_col_defs.append(Blueprint._column_sql(change_map[name], "sqlite"))
        else:
            # Reconstruct SQL from PRAGMA info — sanitize PRAGMA-derived values
            col_type = str(row[2]) if row[2] else "TEXT"
            if not _SAFE_TYPE_RE.match(col_type):
                col_type = "TEXT"
            col_sql = f"{_ident(name)} {col_type}"
            if row[5]:  # pk
                col_sql += " PRIMARY KEY"
            if row[3] and not row[5]:  # notnull, not pk
                col_sql += " NOT NULL"
            if row[4] is not None:
                raw_default = str(row[4])
                # Only allow simple literals: numbers, quoted strings, NULL, TRUE, FALSE
                if re.match(r"^(-?\d+(\.\d+)?|'[^']*'|NULL|TRUE|FALSE|CURRENT_TIMESTAMP)$", raw_default, re.IGNORECASE):
                    col_sql += f" DEFAULT {raw_default}"
            new_col_defs.append(col_sql)

    # All identifiers from PRAGMA are from the DB itself, so they are trusted;
    # we still validate them to be safe.
    safe_col_names = [_ident(n) for n in col_names]
    tmp = f"_hunt_rebuild_{_ident(table)}"
    conn.execute(text(f"DROP TABLE IF EXISTS {tmp}"))
    create = f"CREATE TABLE {tmp} (\n  " + ",\n  ".join(new_col_defs) + "\n)"
    conn.execute(text(create))
    cols_str = ", ".join(safe_col_names)
    conn.execute(text(f"INSERT INTO {tmp} ({cols_str}) SELECT {cols_str} FROM {_ident(table)}"))
    conn.execute(text(f"DROP TABLE {_ident(table)}"))
    conn.execute(text(f"ALTER TABLE {tmp} RENAME TO {_ident(table)}"))


def _pg_alter_column(conn, table: str, col: ColumnDef) -> None:
    """Issue the necessary ALTER COLUMN statements for PostgreSQL."""
    t = _ident(table)
    c = _ident(col.name)
    # Type change
    pg_type = col.type
    if col.length:
        pg_type = f"{col.type}({col.length})"
    conn.execute(text(f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {pg_type}"))

    # Nullability
    if col.is_nullable:
        conn.execute(text(f"ALTER TABLE {t} ALTER COLUMN {c} DROP NOT NULL"))
    else:
        conn.execute(text(f"ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL"))

    # Default
    if col.default_value is not None:
        default_val = _safe_default(col.default_value, "postgresql", col.type)
        conn.execute(text(f"ALTER TABLE {t} ALTER COLUMN {c} SET DEFAULT {default_val}"))
    else:
        conn.execute(text(f"ALTER TABLE {t} ALTER COLUMN {c} DROP DEFAULT"))


def _drop_column_if_exists(conn, table: str, col_name: str, dialect: str) -> None:
    """Drop a column only if it exists; handles SQLite's lack of IF EXISTS syntax."""
    t = _ident(table)
    c = _ident(col_name)
    if dialect == "sqlite":
        result = conn.execute(text(f"PRAGMA table_info({t})"))
        existing = {row[1] for row in result.fetchall()}
        if col_name not in existing:
            return
        conn.execute(text(f"ALTER TABLE {t} DROP COLUMN {c}"))
    else:
        conn.execute(text(f"ALTER TABLE {t} DROP COLUMN IF EXISTS {c}"))
