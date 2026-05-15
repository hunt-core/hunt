from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_connections: dict[str, Engine] = {}
_default: str = "sqlite"


def configure(configs: dict[str, Any]) -> None:
    global _default
    _default = configs.get("default", "sqlite")


def connection(name: str | None = None) -> Engine:
    name = name or _default
    if name not in _connections:
        _connections[name] = _make_engine(name)
    return _connections[name]


def _make_engine(name: str) -> Engine:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return create_engine(db_url)

    driver = os.environ.get("DB_CONNECTION", "sqlite")
    if driver == "sqlite":
        db_name = os.environ.get("DB_DATABASE", ":memory:")
        if db_name == ":memory:":
            return create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        return create_engine(f"sqlite:///{db_name}", connect_args={"check_same_thread": False})
    if driver == "mysql":
        user = quote_plus(os.environ.get("DB_USERNAME", "root"))
        password = quote_plus(os.environ.get("DB_PASSWORD", ""))
        host = os.environ.get("DB_HOST", "127.0.0.1")
        port = os.environ.get("DB_PORT", "3306")
        db = os.environ.get("DB_DATABASE", "hunt")
        return create_engine(f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}")
    if driver == "postgresql" or driver == "pgsql":
        user = quote_plus(os.environ.get("DB_USERNAME", "postgres"))
        password = quote_plus(os.environ.get("DB_PASSWORD", ""))
        host = os.environ.get("DB_HOST", "127.0.0.1")
        port = os.environ.get("DB_PORT", "5432")
        db = os.environ.get("DB_DATABASE", "hunt")
        return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}")

    raise ValueError(f"Unsupported DB driver: {driver}")


def raw(sql: str, bindings: dict | None = None, conn_name: str | None = None) -> Any:
    engine = connection(conn_name)
    with engine.connect() as conn:
        result = conn.execute(text(sql), bindings or {})
        conn.commit()
        return result


def transaction(callback, conn_name: str | None = None) -> Any:
    """Run callback inside a database transaction. Rolls back on exception."""
    engine = connection(conn_name)
    with engine.begin() as conn:
        return callback(conn)


class _TransactionContext:
    """Context manager for explicit transaction control."""

    def __init__(self, conn_name: str | None = None) -> None:
        self._conn_name = conn_name
        self._conn = None
        self._tx = None

    def __enter__(self):
        engine = connection(self._conn_name)
        self._conn = engine.connect()
        self._tx = self._conn.begin()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._tx.rollback()
        else:
            self._tx.commit()
        self._conn.close()
        return False


def begin(conn_name: str | None = None) -> _TransactionContext:
    """Return a context manager for a manual transaction block."""
    return _TransactionContext(conn_name)
