from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_connections: dict[str, Engine] = {}
_connections_lock = threading.RLock()
_config: dict[str, Any] = {}

# Set while the eager loader calls a model method to obtain its Relation
# object. A real relationship method only builds a Relation, so any attempt to
# reach the database during that call is refused (see QueryBuilder.with_).
_RELATION_PROBE: ContextVar[bool] = ContextVar("hunt_db_relation_probe", default=False)


def _refuse_during_relation_probe() -> None:
    if _RELATION_PROBE.get():
        raise RuntimeError("Database access is not allowed while resolving a relationship for eager loading.")


def configure(configs: dict[str, Any]) -> None:
    """Apply the `database` config section (config/database.py)."""
    global _config
    _config = configs or {}


def _default_name() -> str:
    if _config:
        return _config.get("default") or "sqlite"
    return os.environ.get("DB_CONNECTION", "sqlite")


def connection(name: str | None = None) -> Engine:
    _refuse_during_relation_probe()
    name = name or _default_name()
    engine = _connections.get(name)
    if engine is None:
        # Double-checked under a lock so two threads never build (and leak) two
        # engines, each with its own pool, for the same connection name.
        with _connections_lock:
            engine = _connections.get(name)
            if engine is None:
                engine = _make_engine(name)
                _connections[name] = engine
    return engine


def _pool_kwargs() -> dict:
    """Read pool configuration from environment variables."""
    return {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "3600")),
        # Test a pooled connection before handing it out, so a connection the
        # server closed (idle timeout, restart) is replaced instead of failing
        # the first query that uses it.
        "pool_pre_ping": True,
    }


def _enable_sqlite_foreign_keys(engine: Engine) -> Engine:
    """SQLite ignores FOREIGN KEY constraints unless each connection opts in."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def _sqlite_url_engine(url: str) -> Engine:
    # SQLite URLs must not use pooling kwargs
    return _enable_sqlite_foreign_keys(create_engine(url, connect_args={"check_same_thread": False}))


def _sqlite_engine(db_name: str) -> Engine:
    if db_name == ":memory:":
        return _enable_sqlite_foreign_keys(
            create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        )
    return _sqlite_url_engine(f"sqlite:///{db_name}")


def _server_engine(scheme: str, user: str, password: str, host: str, port: str, db: str) -> Engine:
    return create_engine(f"{scheme}://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}", **_pool_kwargs())


def _make_engine(name: str) -> Engine:
    # DATABASE_URL describes the application's default database only. A named
    # secondary connection must come from config/database.py (or the DB_* env
    # fallbacks), otherwise every "other" connection would silently point at
    # the default database.
    db_url = os.environ.get("DATABASE_URL")
    if db_url and name == _default_name():
        if db_url.startswith("sqlite"):
            return _sqlite_url_engine(db_url)
        return create_engine(db_url, **_pool_kwargs())

    cfg = _config.get("connections", {}).get(name) if _config else None
    if isinstance(cfg, dict):
        return _engine_from_config(name, cfg)

    # No config for this connection — build from DB_* env vars, treating the
    # connection name as the driver.
    driver = name
    if driver == "sqlite":
        return _sqlite_engine(os.environ.get("DB_DATABASE", ":memory:"))
    if driver == "mysql":
        return _server_engine(
            "mysql+pymysql",
            os.environ.get("DB_USERNAME", "root"),
            os.environ.get("DB_PASSWORD", ""),
            os.environ.get("DB_HOST", "127.0.0.1"),
            os.environ.get("DB_PORT", "3306"),
            os.environ.get("DB_DATABASE", "hunt"),
        )
    if driver == "postgresql" or driver == "pgsql":
        return _server_engine(
            "postgresql+psycopg2",
            os.environ.get("DB_USERNAME", "postgres"),
            os.environ.get("DB_PASSWORD", ""),
            os.environ.get("DB_HOST", "127.0.0.1"),
            os.environ.get("DB_PORT", "5432"),
            os.environ.get("DB_DATABASE", "hunt"),
        )

    raise ValueError(f"Unsupported DB driver: {driver}")


def _engine_from_config(name: str, cfg: dict[str, Any]) -> Engine:
    if "url" in cfg:
        url = str(cfg["url"])
        if url.startswith("sqlite"):
            return _sqlite_url_engine(url)
        return create_engine(url, **_pool_kwargs())

    driver = cfg.get("driver", name)
    if driver == "sqlite":
        return _sqlite_engine(str(cfg.get("database", ":memory:")))
    if driver == "mysql":
        return _server_engine(
            "mysql+pymysql",
            str(cfg.get("username", "root")),
            str(cfg.get("password", "")),
            str(cfg.get("host", "127.0.0.1")),
            str(cfg.get("port", "3306")),
            str(cfg.get("database", "hunt")),
        )
    if driver == "postgresql" or driver == "pgsql":
        return _server_engine(
            "postgresql+psycopg2",
            str(cfg.get("username", "postgres")),
            str(cfg.get("password", "")),
            str(cfg.get("host", "127.0.0.1")),
            str(cfg.get("port", "5432")),
            str(cfg.get("database", "hunt")),
        )

    raise ValueError(f"Unsupported DB driver: {driver}")


# ---------------------------------------------------------------------------
# Ambient transactions
#
# ``begin()`` / ``transaction()`` publish their connection through a ContextVar
# so that every Model / QueryBuilder / Schema call made inside the block runs on
# that same connection and is committed or rolled back with it.  A ContextVar is
# per asyncio task and per thread, so concurrent requests never see each other's
# transaction.  The ``async_*`` ORM helpers copy the context into their worker
# thread, so they join the transaction of the task that awaited them.
# ---------------------------------------------------------------------------


class _Ambient:
    """An open transaction's connection plus a lock serialising access to it."""

    __slots__ = ("conn", "lock")

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        # A DBAPI connection must not run two statements at once.  The lock
        # matters when several ``async_*`` calls are awaited concurrently
        # inside one transaction (each runs in its own worker thread).
        self.lock = threading.RLock()


_ambient: ContextVar[dict[Any, _Ambient] | None] = ContextVar("hunt_db_ambient", default=None)

# Process-wide fallback used by the testing helpers (``DatabaseTransactions``):
# a connection pinned here is used by *every* thread and task, so code under
# test cannot escape the test's transaction.
_pinned: dict[Any, _Ambient] = {}


def _resolve_engine(target: Any = None) -> Any:
    """Accept a connection name (or None for the default) or an Engine."""
    if target is None or isinstance(target, str):
        return connection(target)
    return target


def _ambient_for(engine: Any) -> _Ambient | None:
    current = _ambient.get()
    if current:
        amb = current.get(engine)
        if amb is not None:
            return amb
    return _pinned.get(engine)


def in_transaction(conn_name: Any = None) -> bool:
    """True when the calling task/thread is inside ``begin()`` / ``transaction()`` for this connection."""
    return _ambient_for(_resolve_engine(conn_name)) is not None


@contextlib.contextmanager
def use_connection(target: Any = None, *, commit: bool = True) -> Iterator[Any]:
    """Yield the connection that a statement should run on.

    Inside ``begin()`` / ``transaction()`` this is the transaction's own
    connection and nothing is committed here: the transaction decides.
    Otherwise a fresh connection is checked out, committed (unless
    ``commit=False``) and returned to the pool.

    *target* is a connection name, ``None`` for the default, or an Engine.
    """
    _refuse_during_relation_probe()
    engine = _resolve_engine(target)
    amb = _ambient_for(engine)
    if amb is not None:
        with amb.lock:
            yield amb.conn
        return
    with engine.connect() as conn:
        yield conn
        if commit:
            conn.commit()


def _sqlite_begin(conn: Any, engine: Any, statement: str = "BEGIN") -> None:
    """Open a real SQLite transaction.

    The stdlib sqlite3 driver only starts a transaction lazily before
    INSERT/UPDATE/DELETE, so DDL and SAVEPOINT would otherwise run in
    autocommit mode and could not be rolled back.
    """
    if getattr(getattr(engine, "dialect", None), "name", None) != "sqlite":
        return
    dbapi_conn = getattr(getattr(conn, "connection", None), "dbapi_connection", None)
    if getattr(dbapi_conn, "in_transaction", None) is False:
        conn.exec_driver_sql(statement)


def _open_transaction(engine: Any, sqlite_begin: str = "BEGIN") -> tuple[Any, Any]:
    conn = engine.connect()
    try:
        tx = conn.begin()
        _sqlite_begin(conn, engine, sqlite_begin)
    except BaseException:
        conn.close()
        raise
    return conn, tx


def _exec_verbatim(conn: Any, sql: str) -> Any:
    """Execute a statement exactly as written: no ``:name`` bind parsing, no ``%`` formatting."""
    return conn.exec_driver_sql(sql, execution_options={"no_parameters": True})


def _sqlite_fk_violations(conn: Any) -> set[tuple]:
    return {tuple(row) for row in _exec_verbatim(conn, "PRAGMA foreign_key_check").fetchall()}


def _sqlite_assert_no_new_fk_violations(conn: Any, before: set[tuple]) -> None:
    new = _sqlite_fk_violations(conn) - before
    if new:
        tables = sorted({str(row[0]) for row in new})
        raise RuntimeError(f"Schema change would break foreign keys in: {', '.join(tables)}. Nothing was changed.")


def _sqlite_suspend_foreign_keys(conn: Any) -> set[tuple] | None:
    """Turn SQLite foreign-key enforcement off on *conn* (must be outside a transaction).

    Returns the set of violations that already existed, or None when
    enforcement was not on (nothing to restore, nothing to check).
    """
    fk_on = bool(_exec_verbatim(conn, "PRAGMA foreign_keys").scalar())
    conn.commit()
    if not fk_on:
        return None
    _exec_verbatim(conn, "PRAGMA foreign_keys=OFF")
    conn.commit()
    before = _sqlite_fk_violations(conn)
    conn.commit()
    return before


def _sqlite_resume_foreign_keys(conn: Any) -> None:
    _exec_verbatim(conn, "PRAGMA foreign_keys=ON")
    conn.commit()


def raw(sql: str, bindings: dict | None = None, conn_name: str | None = None) -> Any:
    with use_connection(connection(conn_name)) as conn:
        return conn.execute(text(sql), bindings or {})


def transaction(callback, conn_name: str | None = None) -> Any:
    """Run callback inside a database transaction. Rolls back on exception.

    Model, QueryBuilder and Schema calls made by the callback join the
    transaction.  Calling ``transaction()`` / ``begin()`` again inside it
    creates a savepoint.
    """
    with _TransactionContext(conn_name) as conn:
        return callback(conn)


class _TransactionContext:
    """Context manager for explicit transaction control."""

    def __init__(
        self,
        conn_name: str | None = None,
        *,
        engine: Any = None,
        sqlite_begin: str = "BEGIN",
        schema_change: bool = False,
    ) -> None:
        self._conn_name = conn_name
        self._sqlite_begin = sqlite_begin
        # schema_change: on SQLite, suspend foreign-key enforcement for the
        # transaction (table rebuilds need it) and verify with
        # ``foreign_key_check`` before committing.
        self._schema_change = schema_change
        self._engine: Any = engine
        self._conn = None
        self._tx = None
        self._token = None
        self._nested = False
        self._fk_before: set[tuple] | None = None

    def __enter__(self):
        engine = self._engine if self._engine is not None else connection(self._conn_name)
        self._engine = engine
        amb = _ambient_for(engine)
        if amb is not None:
            # Already inside a transaction on this connection: use a savepoint
            # so an inner failure rolls back only the inner block.
            self._nested = True
            self._conn = amb.conn
            with amb.lock:
                self._tx = amb.conn.begin_nested()
            return self._conn

        if self._schema_change and getattr(getattr(engine, "dialect", None), "name", None) == "sqlite":
            conn = engine.connect()
            try:
                self._fk_before = _sqlite_suspend_foreign_keys(conn)
                tx = conn.begin()
                _sqlite_begin(conn, engine, self._sqlite_begin)
            except BaseException:
                self._restore_foreign_keys(conn)
                conn.close()
                raise
            self._conn, self._tx = conn, tx
        else:
            self._conn, self._tx = _open_transaction(engine, self._sqlite_begin)
        current = dict(_ambient.get() or {})
        current[engine] = _Ambient(self._conn)
        self._token = _ambient.set(current)
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                self._tx.rollback()
            else:
                if self._fk_before is not None:
                    try:
                        _sqlite_assert_no_new_fk_violations(self._conn, self._fk_before)
                    except BaseException:
                        self._tx.rollback()
                        raise
                self._tx.commit()
        finally:
            if not self._nested:
                try:
                    self._clear_ambient()
                finally:
                    # Always return the connection to the pool, even when the
                    # commit or rollback above raised.
                    try:
                        self._restore_foreign_keys(self._conn)
                    finally:
                        self._conn.close()
        return False

    def _restore_foreign_keys(self, conn: Any) -> None:
        if self._fk_before is not None:
            self._fk_before = None
            _sqlite_resume_foreign_keys(conn)

    def _clear_ambient(self) -> None:
        try:
            _ambient.reset(self._token)
        except ValueError:
            # Exited in a different context than it was entered in (for
            # example a fixture's setup and teardown): drop the entry by hand.
            current = dict(_ambient.get() or {})
            current.pop(self._engine, None)
            _ambient.set(current or None)


def begin(conn_name: str | None = None) -> _TransactionContext:
    """Return a context manager for a manual transaction block.

    Model, QueryBuilder and Schema calls made inside the block join the
    transaction; a nested ``begin()`` creates a savepoint.
    """
    return _TransactionContext(conn_name)


def _pin_connection(conn_name: str | None = None) -> tuple[Any, Any, Any]:
    """Open a transaction and make it the connection for every thread and task.

    Testing helper: returns ``(engine, conn, tx)``; pass them to
    ``_unpin_connection`` to roll back and release.
    """
    engine = connection(conn_name)
    if engine in _pinned:
        raise RuntimeError("A connection is already pinned for this database.")
    conn, tx = _open_transaction(engine)
    _pinned[engine] = _Ambient(conn)
    return engine, conn, tx


def _unpin_connection(engine: Any, conn: Any, tx: Any, *, commit: bool = False) -> None:
    try:
        if commit:
            tx.commit()
        else:
            tx.rollback()
    finally:
        _pinned.pop(engine, None)
        conn.close()
