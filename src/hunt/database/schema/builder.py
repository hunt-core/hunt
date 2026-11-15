from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

from sqlalchemy import text

from hunt.database.connection import (
    _ambient_for,
    _exec_verbatim,
    _sqlite_assert_no_new_fk_violations,
    _sqlite_begin,
    _sqlite_resume_foreign_keys,
    _sqlite_suspend_foreign_keys,
    connection,
    use_connection,
)
from hunt.database.identifiers import simple_ident as _ident
from hunt.database.schema.blueprint import Blueprint, ColumnDef, ForeignKeyDef

# Connection name that Schema calls should use for the current task/thread.
# Set by ``Schema.on(...)`` (the migrator uses it for ``Migrator(conn_name=...)``).
_schema_connection: ContextVar[str | None] = ContextVar("hunt_schema_connection", default=None)


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
        if dialect == "mysql":
            # MySQL treats a backslash inside a string literal as an escape.
            escaped = escaped.replace("\\", "\\\\")
        return f"'{escaped}'"
    raise TypeError(f"Unsupported default value type: {type(value).__name__!r}")


def _ddl(conn: Any, sql: str) -> Any:
    """Execute a DDL statement verbatim.

    DDL is built from validated identifiers and escaped literals, never from
    bind parameters, so it must not go through ``text()``: a ``:word`` inside
    a column default would be parsed as a bind parameter (and a ``%`` would be
    treated as a format character by the MySQL / PostgreSQL drivers).
    """
    return _exec_verbatim(conn, sql)


@contextlib.contextmanager
def _ddl_connection(engine: Any, *, sqlite_rebuild: bool = False) -> Iterator[Any]:
    """Yield the connection for a schema change.

    Normally this is the ambient transaction's connection, or a fresh
    committed one. A SQLite table rebuild outside a transaction follows
    SQLite's documented procedure instead: foreign keys off, one transaction
    around the whole change, ``foreign_key_check``, commit, foreign keys back on.
    """
    dialect = getattr(getattr(engine, "dialect", None), "name", None)
    if not sqlite_rebuild or dialect != "sqlite" or _ambient_for(engine) is not None:
        with use_connection(engine) as conn:
            yield conn
        return

    with engine.connect() as conn:
        # Foreign keys can only be switched outside a transaction.
        fk_before = _sqlite_suspend_foreign_keys(conn)
        try:
            _sqlite_begin(conn, engine)
            try:
                yield conn
                if fk_before is not None:
                    _sqlite_assert_no_new_fk_violations(conn, fk_before)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            if fk_before is not None:
                _sqlite_resume_foreign_keys(conn)


def _sqlite_referencing_tables(conn: Any, table: str) -> list[str]:
    """Names of the other tables that have a foreign key pointing at *table*."""
    referencing = []
    names = [r[0] for r in _ddl(conn, "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for name in names:
        if name == table or str(name).startswith("sqlite_"):
            continue
        fks = _ddl(conn, f"PRAGMA foreign_key_list({_quote(str(name), 'sqlite')})").fetchall()
        if any(str(fk[2]).lower() == table.lower() for fk in fks):
            referencing.append(str(name))
    return referencing


class Schema:
    _conn_name: str | None = None

    @classmethod
    def _engine(cls) -> Any:
        return connection(_schema_connection.get() or cls._conn_name)

    @classmethod
    @contextlib.contextmanager
    def on(cls, conn_name: str | None) -> Iterator[type[Schema]]:
        """Run the Schema calls inside the block against a named connection::

        with Schema.on("analytics"):
            Schema.create("events", ...)
        """
        token = _schema_connection.set(conn_name)
        try:
            yield cls
        finally:
            _schema_connection.reset(token)

    @classmethod
    def create(cls, table: str, callback: Callable[[Blueprint], None]) -> None:
        bp = Blueprint(_ident(table))
        callback(bp)
        engine = cls._engine()
        dialect = engine.dialect.name
        with use_connection(engine) as conn:
            for sql in bp.to_create_sql(dialect):
                _ddl(conn, sql)

    @classmethod
    def table(cls, table: str, callback: Callable[[Blueprint], None]) -> None:
        """Alter an existing table.

        Adds / changes / drops / renames columns, creates and drops indexes
        and unique constraints, adds and drops foreign keys and a composite
        primary key, and renames the table.
        """
        t = _ident(table)
        bp = Blueprint(t)
        callback(bp)
        engine = cls._engine()
        dialect = engine.dialect.name  # 'sqlite', 'mysql', 'postgresql'

        new_cols = [c for c in bp.columns if not c.is_change]
        change_cols = [c for c in bp.columns if c.is_change]
        # Foreign keys declared on new columns via .references().on(), plus
        # explicit bp.foreign(...) builders.
        add_fks = bp.foreign_key_defs()
        drop_fks = list(bp._drop_foreign_keys)

        if dialect == "sqlite":
            # SQLite can add a REFERENCES clause inline with ADD COLUMN, but
            # only for a column whose default is NULL; every other foreign-key
            # or primary-key change needs a table rebuild.
            inline_fk_cols = {
                c.name for c in new_cols if c.foreign_key() is not None and c.is_nullable and c.default_value is None
            }
            rebuild_fks = [fk for fk in add_fks if fk.column not in inline_fk_cols]
            inline_fks = {fk.column: fk for fk in add_fks if fk.column in inline_fk_cols}
            needs_rebuild = bool(change_cols or rebuild_fks or drop_fks or bp._primary_columns)
        else:
            rebuild_fks, inline_fks, needs_rebuild = [], {}, False

        with _ddl_connection(engine, sqlite_rebuild=needs_rebuild) as conn:
            # 1. Add new columns
            for col in new_cols:
                sql = f"ALTER TABLE {t} ADD COLUMN {Blueprint._column_sql(col, dialect)}"
                if col.name in inline_fks:
                    fk = inline_fks[col.name]
                    sql += f" REFERENCES {_ident(fk.ref_table)}({_ident(fk.ref_column)})"
                    if fk.on_delete:
                        sql += f" ON DELETE {fk.on_delete}"
                    if fk.on_update:
                        sql += f" ON UPDATE {fk.on_update}"
                if dialect == "mysql" and col.after_column:
                    sql += f" AFTER {_ident(col.after_column)}"
                _ddl(conn, sql)

            # 2. Alter existing columns (and, on SQLite, every change that needs a rebuild)
            if dialect == "sqlite":
                if needs_rebuild:
                    _sqlite_rebuild_columns(
                        conn,
                        t,
                        change_cols,
                        add_foreign_keys=rebuild_fks,
                        drop_foreign_keys=drop_fks,
                        primary_columns=bp._primary_columns,
                    )
            elif dialect == "mysql":
                for col in change_cols:
                    sql = f"ALTER TABLE {t} MODIFY COLUMN {Blueprint._column_sql(col, dialect)}"
                    if col.after_column:
                        sql += f" AFTER {_ident(col.after_column)}"
                    _ddl(conn, sql)
            else:  # postgresql and others
                for col in change_cols:
                    _pg_alter_column(conn, t, col)

            # 3. Drop foreign keys and indexes (before the columns they cover)
            if dialect != "sqlite":
                for fk_name in drop_fks:
                    if dialect == "mysql":
                        _ddl(conn, f"ALTER TABLE {t} DROP FOREIGN KEY {_ident(fk_name)}")
                    else:
                        _ddl(conn, f"ALTER TABLE {t} DROP CONSTRAINT {_ident(fk_name)}")
            for idx_name in bp._drop_indexes:
                if dialect == "mysql":
                    _ddl(conn, f"DROP INDEX {_ident(idx_name)} ON {t}")
                else:
                    _ddl(conn, f"DROP INDEX {_ident(idx_name)}")

            # 4. Drop columns
            for col_name in bp._drop_columns:
                _ddl(conn, f"ALTER TABLE {t} DROP COLUMN {_ident(col_name)}")
            for col_name in bp._drop_columns_if_exists:
                _drop_column_if_exists(conn, t, col_name, dialect)

            # 5. Rename columns
            for old_name, new_name in bp._rename_columns:
                _ddl(conn, f"ALTER TABLE {t} RENAME COLUMN {_ident(old_name)} TO {_ident(new_name)}")

            # 6. Primary key, indexes, foreign keys
            if dialect != "sqlite":
                if bp._primary_columns:
                    cols = ", ".join(_ident(c) for c in bp._primary_columns)
                    _ddl(conn, f"ALTER TABLE {t} ADD PRIMARY KEY ({cols})")
            for sql in bp.index_sql(dialect, if_not_exists=False):
                _ddl(conn, sql)
            if dialect != "sqlite":
                for fk in add_fks:
                    _ddl(conn, f"ALTER TABLE {t} ADD {fk.to_sql(t)}")

            # 7. Rename table (must be last)
            if bp._rename_to:
                _ddl(conn, f"ALTER TABLE {t} RENAME TO {_ident(bp._rename_to)}")

    @classmethod
    def drop(cls, table: str) -> None:
        with use_connection(cls._engine()) as conn:
            _ddl(conn, f"DROP TABLE {_ident(table)}")

    @classmethod
    def drop_if_exists(cls, table: str) -> None:
        with use_connection(cls._engine()) as conn:
            _ddl(conn, f"DROP TABLE IF EXISTS {_ident(table)}")

    @classmethod
    def has_table(cls, table: str) -> bool:
        engine = cls._engine()
        dialect = engine.dialect.name
        with use_connection(engine, commit=False) as conn:
            if dialect == "postgresql":
                result = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables WHERE table_name=:t AND table_schema=current_schema()"
                    ),
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
        engine = cls._engine()
        t = _ident(table)
        dialect = engine.dialect.name
        with use_connection(engine, commit=False) as conn:
            if dialect == "postgresql":
                result = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_name=:t AND column_name=:c AND table_schema=current_schema()"
                    ),
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
                result = _ddl(conn, f"PRAGMA table_info({t})")
                return any(row[1] == column for row in result.fetchall())

    @classmethod
    def rename(cls, from_table: str, to_table: str) -> None:
        """Rename a table (convenience method — no Blueprint needed)."""
        with use_connection(cls._engine()) as conn:
            _ddl(conn, f"ALTER TABLE {_ident(from_table)} RENAME TO {_ident(to_table)}")

    @classmethod
    def rename_column(cls, table: str, from_col: str, to_col: str) -> None:
        """Rename a single column (convenience method — no Blueprint needed)."""
        with use_connection(cls._engine()) as conn:
            _ddl(conn, f"ALTER TABLE {_ident(table)} RENAME COLUMN {_ident(from_col)} TO {_ident(to_col)}")

    @classmethod
    def drop_all_tables(cls) -> list[str]:
        """Drop every table (and view) in the connection's database / schema.

        Foreign-key checks are suspended for the duration, so the order of the
        drops does not matter. Returns the names of the dropped tables.
        """
        from sqlalchemy import inspect as sa_inspect

        engine = cls._engine()
        dialect = engine.dialect.name
        if dialect == "sqlite" and _ambient_for(engine) is not None:
            raise RuntimeError("Schema.drop_all_tables() cannot run inside a transaction on SQLite.")

        with use_connection(engine) as conn:
            inspector = sa_inspect(conn)
            views = list(inspector.get_view_names())
            tables = [name for name in inspector.get_table_names() if not name.startswith("sqlite_")]
            fk_before = None
            if dialect == "sqlite":
                fk_before = _sqlite_suspend_foreign_keys(conn)
            elif dialect == "mysql":
                _ddl(conn, "SET FOREIGN_KEY_CHECKS=0")
            try:
                for view in views:
                    _ddl(conn, f"DROP VIEW IF EXISTS {_quote(view, dialect)}")
                cascade = " CASCADE" if dialect == "postgresql" else ""
                for name in tables:
                    _ddl(conn, f"DROP TABLE IF EXISTS {_quote(name, dialect)}{cascade}")
                conn.commit()
            finally:
                if fk_before is not None:
                    _sqlite_resume_foreign_keys(conn)
                elif dialect == "mysql":
                    _ddl(conn, "SET FOREIGN_KEY_CHECKS=1")
        return tables


def _quote(name: str, dialect: str) -> str:
    """Quote an identifier that came from the database catalog (not from user input)."""
    if dialect == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Driver-specific ALTER COLUMN helpers
# ---------------------------------------------------------------------------

_TABLE_CONSTRAINT_WORDS = ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN")


def _split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on top-level commas (outside parentheses and quotes)."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            current.append(ch)
            if ch == quote:
                if quote != "]" and i + 1 < len(body) and body[i + 1] == quote:
                    current.append(body[i + 1])  # doubled quote = escaped quote
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
            current.append(ch)
        elif ch == "[":
            quote = "]"
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_create_table(sql: str) -> tuple[list[str], str]:
    """Return (definitions, suffix) of a CREATE TABLE statement.

    *definitions* are the column definitions and table constraints, verbatim;
    *suffix* is whatever follows the closing parenthesis (``WITHOUT ROWID`` ...).
    """
    start = sql.find("(")
    if start == -1:
        raise RuntimeError("Could not parse the table's CREATE TABLE statement.")
    depth = 0
    quote: str | None = None
    end = -1
    i = start
    while i < len(sql):
        ch = sql[i]
        if quote:
            if ch == quote:
                if quote != "]" and i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
        elif ch == "[":
            quote = "]"
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    if end == -1:
        raise RuntimeError("Could not parse the table's CREATE TABLE statement.")
    return _split_top_level(sql[start + 1 : end]), sql[end + 1 :].strip()


def _definition_name(definition: str) -> str | None:
    """Column name of a column definition, or None for a table constraint."""
    stripped = definition.lstrip()
    first = re.match(r"[A-Za-z_]+", stripped)
    if first and first.group(0).upper() in _TABLE_CONSTRAINT_WORDS:
        return None
    m = re.match(r'"((?:[^"]|"")*)"|`((?:[^`]|``)*)`|\[([^\]]*)\]|([A-Za-z_][A-Za-z0-9_]*)', stripped)
    if not m:
        return None
    return next(g for g in m.groups() if g is not None)


def _is_foreign_key_for(definition: str, table: str, names: list[str]) -> bool:
    """True when a table-constraint definition is one of the foreign keys to drop."""
    normalised = re.sub(r"\s+", " ", definition.strip())
    if not re.search(r"\bFOREIGN KEY\b", normalised, re.IGNORECASE):
        return False
    m = re.match(r'CONSTRAINT ["`\[]?([A-Za-z_][A-Za-z0-9_]*)["`\]]? ', normalised, re.IGNORECASE)
    if m and m.group(1) in names:
        return True
    cols = re.search(r"FOREIGN KEY ?\(([^)]*)\)", normalised, re.IGNORECASE)
    if cols:
        col_names = [c.strip().strip('"`[]') for c in cols.group(1).split(",")]
        return f"{table}_{'_'.join(col_names)}_foreign" in names
    return False


def _sqlite_rebuild_columns(
    conn: Any,
    table: str,
    changes: list[ColumnDef],
    *,
    add_foreign_keys: list[ForeignKeyDef] | None = None,
    drop_foreign_keys: list[str] | None = None,
    primary_columns: list[str] | None = None,
) -> None:
    """
    SQLite does not support ALTER COLUMN (or adding constraints); rebuild the table.

    Follows SQLite's documented procedure ("Making Other Kinds Of Table Schema
    Changes"): the new table is created from the *original* ``CREATE TABLE``
    statement with only the changed column definitions replaced, so every other
    column keeps its exact definition (AUTOINCREMENT, CHECK, UNIQUE, REFERENCES,
    defaults) and all table constraints survive. Indexes and triggers are
    recreated afterwards from their stored DDL.
    """
    t = _ident(table)
    row = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"), {"t": t}).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"Table '{table}' not found.")
    definitions, suffix = _parse_create_table(str(row[0]))

    info = _ddl(conn, f"PRAGMA table_info({t})").fetchall()
    # Each row: (cid, name, type, notnull, dflt_value, pk)
    col_names = [r[1] for r in info]
    if not col_names:
        raise RuntimeError(f"Table '{table}' not found or has no columns.")

    change_map = {c.name: c for c in changes}
    unknown = [name for name in change_map if name not in col_names]
    if unknown:
        raise RuntimeError(f"Cannot change column(s) {unknown} on '{table}': no such column.")
    drop_fk_names = list(drop_foreign_keys or [])

    if _ddl(conn, "PRAGMA foreign_keys").scalar():
        # Still enforced: we are inside a caller's transaction, where SQLite
        # ignores PRAGMA foreign_keys=OFF. Dropping a table other tables point
        # at would then fail (or cascade), so refuse up front.
        referencing = _sqlite_referencing_tables(conn, t)
        if referencing:
            raise RuntimeError(
                f"Cannot rebuild SQLite table '{table}' inside a transaction while foreign keys are enforced: "
                f"it is referenced by {', '.join(referencing)}. Run this schema change outside transaction() / "
                "begin() (migrations run by the migrator are handled automatically)."
            )

    new_defs: list[str] = []
    for definition in definitions:
        name = _definition_name(definition)
        if name is not None and name in change_map:
            new_defs.append(Blueprint._column_sql(change_map[name], "sqlite"))
        elif name is None and drop_fk_names and _is_foreign_key_for(definition, t, drop_fk_names):
            continue
        else:
            new_defs.append(definition)
    if primary_columns:
        new_defs.append(f"PRIMARY KEY ({', '.join(_ident(c) for c in primary_columns)})")
    for fk in add_foreign_keys or []:
        new_defs.append(fk.to_sql(t))

    # Index and trigger DDL is dropped with the table: keep it to replay.
    dependents = conn.execute(
        text(
            "SELECT sql FROM sqlite_master"
            " WHERE tbl_name=:t AND type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY type, name"
        ),
        {"t": t},
    ).fetchall()

    # AUTOINCREMENT high-water mark: copying the rows only restores MAX(id),
    # which would let the id of a deleted row be handed out again.
    sequence = None
    if conn.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")).fetchone():
        seq_row = conn.execute(text("SELECT seq FROM sqlite_sequence WHERE name=:t"), {"t": t}).fetchone()
        sequence = seq_row[0] if seq_row else None

    # All identifiers from PRAGMA are from the DB itself, so they are trusted;
    # we still quote them to be safe.
    cols_str = ", ".join(_quote(n, "sqlite") for n in col_names)
    tmp = f"_hunt_rebuild_{t}"
    _ddl(conn, f"DROP TABLE IF EXISTS {tmp}")
    create = f"CREATE TABLE {tmp} (\n  " + ",\n  ".join(new_defs) + "\n)"
    if suffix:
        create += f" {suffix}"
    _ddl(conn, create)
    _ddl(conn, f"INSERT INTO {tmp} ({cols_str}) SELECT {cols_str} FROM {t}")
    _ddl(conn, f"DROP TABLE {t}")
    # With the legacy behaviour the rename does not try to rewrite (or
    # validate) views and triggers that mention the table mid-rebuild.
    legacy = _ddl(conn, "PRAGMA legacy_alter_table").scalar()
    _ddl(conn, "PRAGMA legacy_alter_table=ON")
    try:
        _ddl(conn, f"ALTER TABLE {tmp} RENAME TO {t}")
    finally:
        if not legacy:
            _ddl(conn, "PRAGMA legacy_alter_table=OFF")
    if sequence is not None:
        updated = conn.execute(
            text("UPDATE sqlite_sequence SET seq = :s WHERE name = :t AND seq < :s"), {"s": sequence, "t": t}
        )
        if (
            updated.rowcount == 0
            and not conn.execute(text("SELECT 1 FROM sqlite_sequence WHERE name = :t"), {"t": t}).fetchone()
        ):
            conn.execute(text("INSERT INTO sqlite_sequence (name, seq) VALUES (:t, :s)"), {"t": t, "s": sequence})
    for (dependent_sql,) in dependents:
        _ddl(conn, str(dependent_sql))


def _pg_alter_column(conn: Any, table: str, col: ColumnDef) -> None:
    """Issue the necessary ALTER COLUMN statements for PostgreSQL."""
    t = _ident(table)
    c = _ident(col.name)
    # Type change, spelled for PostgreSQL (DATETIME -> TIMESTAMP, TINYINT ->
    # SMALLINT, ...). USING converts existing values when there is no implicit
    # cast between the old and the new type (e.g. VARCHAR -> INTEGER).
    pg_type = Blueprint._type_sql(col, "postgresql")
    _ddl(conn, f"ALTER TABLE {t} ALTER COLUMN {c} DROP DEFAULT")
    _ddl(conn, f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {pg_type} USING {c}::{pg_type}")

    # Nullability
    if col.is_nullable:
        _ddl(conn, f"ALTER TABLE {t} ALTER COLUMN {c} DROP NOT NULL")
    else:
        _ddl(conn, f"ALTER TABLE {t} ALTER COLUMN {c} SET NOT NULL")

    # Default
    if col.default_value is not None:
        default_val = _safe_default(col.default_value, "postgresql", col.type)
        _ddl(conn, f"ALTER TABLE {t} ALTER COLUMN {c} SET DEFAULT {default_val}")


def _drop_column_if_exists(conn: Any, table: str, col_name: str, dialect: str) -> None:
    """Drop a column only if it exists; handles SQLite's lack of IF EXISTS syntax."""
    t = _ident(table)
    c = _ident(col_name)
    if dialect == "sqlite":
        result = _ddl(conn, f"PRAGMA table_info({t})")
        existing = {row[1] for row in result.fetchall()}
        if col_name not in existing:
            return
        _ddl(conn, f"ALTER TABLE {t} DROP COLUMN {c}")
    elif dialect == "mysql":
        # MySQL has no DROP COLUMN IF EXISTS.
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_name=:t AND column_name=:c AND table_schema=DATABASE()"
            ),
            {"t": t, "c": c},
        ).fetchone()
        if exists:
            _ddl(conn, f"ALTER TABLE {t} DROP COLUMN {c}")
    else:
        _ddl(conn, f"ALTER TABLE {t} DROP COLUMN IF EXISTS {c}")
