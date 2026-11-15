from __future__ import annotations

import contextlib
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from hunt.database.connection import _ambient_for, _TransactionContext, connection, use_connection
from hunt.database.schema.builder import Schema, _ddl

MIGRATIONS_TABLE = "hunt_migrations"
_MIGRATIONS_UNIQUE_INDEX = f"{MIGRATIONS_TABLE}_migration_unique"

# Dialects whose DDL takes part in transactions: a migration either applies
# completely (schema + tracking row) or not at all.
_TRANSACTIONAL_DDL = frozenset({"postgresql", "sqlite"})

# Advisory lock identity shared by every migrator on a database.
_PG_LOCK_KEY = 4_857_911_260_337  # arbitrary constant
_MYSQL_LOCK_NAME = "hunt_migrations"
_MYSQL_LOCK_TIMEOUT = 60


class MigrationError(RuntimeError):
    """A migration could not be run or rolled back safely."""


class Migration:
    # Set to False for a migration that cannot run inside a transaction (for
    # example PostgreSQL ``CREATE INDEX CONCURRENTLY``). It is then tracked
    # with a "started" row like on MySQL, and a failure leaves it marked dirty.
    transactional: ClassVar[bool] = True

    def up(self) -> None:
        raise NotImplementedError

    def down(self) -> None:
        pass


class Migrator:
    def __init__(self, migrations_path: Path, conn_name: str | None = None) -> None:
        self._path = migrations_path
        self._conn_name = conn_name
        self._ensure_table()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _engine(self) -> Any:
        return connection(self._conn_name)

    def _dialect(self) -> str:
        return self._engine().dialect.name

    def _query(self, sql: str, params: dict | None = None) -> list:
        with use_connection(self._engine(), commit=False) as conn:
            return list(conn.execute(text(sql), params or {}).fetchall())

    def _write(self, sql: str, params: dict | None = None) -> None:
        with use_connection(self._engine()) as conn:
            conn.execute(text(sql), params or {})

    # ------------------------------------------------------------------
    # Tracking table
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        # The tracking table lives on the migrator's own connection.
        with Schema.on(self._conn_name):
            Schema.create(
                MIGRATIONS_TABLE,
                lambda bp: (
                    bp.id(),
                    bp.string("migration"),
                    bp.integer("batch").nullable(),
                ),
            )
        self._ensure_unique_index()

    def _ensure_unique_index(self) -> None:
        """One tracking row per migration, enforced by the database.

        Added separately from the CREATE TABLE so tracking tables created by
        older versions get it too.
        """
        engine = self._engine()
        try:
            with use_connection(engine) as conn:
                existing = {idx.get("name") for idx in sa_inspect(conn).get_indexes(MIGRATIONS_TABLE)}
                if _MIGRATIONS_UNIQUE_INDEX in existing:
                    return
                _ddl(conn, f"CREATE UNIQUE INDEX {_MIGRATIONS_UNIQUE_INDEX} ON {MIGRATIONS_TABLE} (migration)")
        except Exception:
            # A legacy table that already holds duplicate rows cannot take the
            # index. Migrating must still work; the lock below still applies.
            pass

    def _ran(self) -> list[str]:
        rows = self._query(f"SELECT migration FROM {MIGRATIONS_TABLE} WHERE batch IS NOT NULL ORDER BY id")
        return [row[0] for row in rows]

    def _dirty(self) -> list[str]:
        """Migrations that started (on a dialect without transactional DDL) and never finished."""
        rows = self._query(f"SELECT migration FROM {MIGRATIONS_TABLE} WHERE batch IS NULL ORDER BY id")
        return [row[0] for row in rows]

    def _files(self) -> list[Path]:
        if not self._path.is_dir():
            return []
        # Files starting with "_" (``__init__.py``, private helpers) are not migrations.
        return [f for f in sorted(self._path.glob("*.py")) if not f.name.startswith("_")]

    def _pending(self) -> list[Path]:
        ran = set(self._ran())
        return [f for f in self._files() if f.stem not in ran]

    def _next_batch(self) -> int:
        rows = self._query(f"SELECT MAX(batch) FROM {MIGRATIONS_TABLE}")
        return ((rows[0][0] if rows else None) or 0) + 1

    # ------------------------------------------------------------------
    # Locking: two deploys (or two containers booting) must not migrate at once.
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        engine = self._engine()
        dialect = engine.dialect.name
        if dialect not in ("postgresql", "mysql") or _ambient_for(engine) is not None:
            # SQLite: each migration's transaction starts with BEGIN IMMEDIATE,
            # which is the lock (see _transaction), and re-checks the tracking
            # table once it holds it.
            yield
            return

        # A session-level lock held on its own connection for the whole run.
        with engine.connect() as lock_conn:
            if dialect == "postgresql":
                lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PG_LOCK_KEY})
            else:
                got = lock_conn.execute(
                    text("SELECT GET_LOCK(:n, :t)"), {"n": _MYSQL_LOCK_NAME, "t": _MYSQL_LOCK_TIMEOUT}
                ).scalar()
                if got != 1:
                    raise MigrationError("Another process is running migrations (could not acquire the lock).")
            lock_conn.commit()
            try:
                yield
            finally:
                if dialect == "postgresql":
                    lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_LOCK_KEY})
                else:
                    lock_conn.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": _MYSQL_LOCK_NAME})
                lock_conn.commit()

    def _transaction(self) -> _TransactionContext:
        """One transaction for a migration's schema changes and its tracking row."""
        return _TransactionContext(
            self._conn_name,
            engine=self._engine(),
            sqlite_begin="BEGIN IMMEDIATE",
            schema_change=True,
        )

    def _is_transactional(self, migration: Migration) -> bool:
        return self._dialect() in _TRANSACTIONAL_DDL and bool(getattr(migration, "transactional", True))

    def _assert_clean(self) -> None:
        dirty = self._dirty()
        if dirty:
            raise MigrationError(
                f"Migration(s) {', '.join(dirty)} started but did not finish, so the schema may be partially "
                f"applied. Repair the schema by hand, then either delete the row from {MIGRATIONS_TABLE} (to run "
                "the migration again) or set its batch number (to mark it as done)."
            )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def run(self) -> list[str]:
        with self._lock():
            self._assert_clean()
            # Read the pending list only once the lock is held.
            pending = self._pending()
            if not pending:
                return []

            batch = self._next_batch()
            ran: list[str] = []
            for file in pending:
                if self._run_one(file, batch):
                    ran.append(file.stem)
            return ran

    def _run_one(self, file: Path, batch: int) -> bool:
        migration = self._load(file)
        name = file.stem
        with Schema.on(self._conn_name):
            if self._is_transactional(migration):
                with self._transaction() as conn:
                    already = conn.execute(
                        text(f"SELECT 1 FROM {MIGRATIONS_TABLE} WHERE migration = :m"), {"m": name}
                    ).fetchone()
                    if already:
                        return False  # another process ran it while we waited for the lock
                    migration.up()
                    conn.execute(
                        text(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES (:m, :b)"),
                        {"m": name, "b": batch},
                    )
                return True

            # No transactional DDL (MySQL): a failure cannot be rolled back.
            # Record that the migration started, so a half-applied one is
            # reported as dirty instead of being silently re-run.
            self._write(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES (:m, NULL)", {"m": name})
            migration.up()
            self._write(f"UPDATE {MIGRATIONS_TABLE} SET batch = :b WHERE migration = :m", {"m": name, "b": batch})
            return True

    def rollback(self, force: bool = False) -> list[str]:
        """Roll back the last batch.

        A tracked migration whose file is missing cannot be undone: that raises
        ``MigrationError`` unless *force* is true, in which case only its
        tracking row is removed.
        """
        with self._lock():
            self._assert_clean()
            rows = self._query(f"SELECT MAX(batch) FROM {MIGRATIONS_TABLE}")
            if not rows or rows[0][0] is None:
                return []
            last_batch = rows[0][0]
            names = [
                r[0]
                for r in self._query(
                    f"SELECT migration FROM {MIGRATIONS_TABLE} WHERE batch = :b ORDER BY id DESC",
                    {"b": last_batch},
                )
            ]

            if not force:
                missing = [name for name in names if not (self._path / f"{name}.py").exists()]
                if missing:
                    raise MigrationError(
                        f"Cannot roll back {', '.join(missing)}: the migration file is missing. "
                        "Restore the file, or use --force to drop the tracking row without running down()."
                    )

            rolled: list[str] = []
            for name in names:
                file = self._path / f"{name}.py"
                self._rollback_one(name, file if file.exists() else None)
                rolled.append(name)
            return rolled

    def _rollback_one(self, name: str, file: Path | None) -> None:
        migration = self._load(file) if file is not None else None
        delete_sql = f"DELETE FROM {MIGRATIONS_TABLE} WHERE migration = :m"
        with Schema.on(self._conn_name):
            if migration is None:
                self._write(delete_sql, {"m": name})
            elif self._is_transactional(migration):
                with self._transaction() as conn:
                    migration.down()
                    conn.execute(text(delete_sql), {"m": name})
            else:
                # Mark as dirty while down() runs, so a failure is visible.
                self._write(f"UPDATE {MIGRATIONS_TABLE} SET batch = NULL WHERE migration = :m", {"m": name})
                migration.down()
                self._write(delete_sql, {"m": name})

    def fresh(self) -> list[str]:
        """Drop every table in the database, then run all migrations from scratch."""
        with Schema.on(self._conn_name):
            Schema.drop_all_tables()
        self._ensure_table()
        return self.run()

    def status(self) -> list[dict]:
        ran = set(self._ran())
        dirty = set(self._dirty())
        result = []
        for f in self._files():
            entry: dict[str, Any] = {"migration": f.stem, "ran": f.stem in ran}
            if f.stem in dirty:
                entry["dirty"] = True
            result.append(entry)
        return result

    @staticmethod
    def _load(file: Path) -> Migration:
        spec = importlib.util.spec_from_file_location(file.stem, file)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
        candidates = [
            obj
            for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, Migration) and obj is not Migration
        ]
        # Prefer the class the file itself defines over Migration subclasses it
        # merely imports (a shared base class, another migration).
        own = [obj for obj in candidates if obj.__module__ == module.__name__]
        chosen = own or candidates
        if not chosen:
            raise ValueError(f"No Migration subclass found in {file}")
        # With several, the last one defined is the concrete migration (base
        # classes come first in the file).
        return chosen[-1]()
