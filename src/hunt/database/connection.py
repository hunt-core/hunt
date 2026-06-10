from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

_connections: dict[str, Engine] = {}
_config: dict[str, Any] = {}


def configure(configs: dict[str, Any]) -> None:
    """Apply the `database` config section (config/database.py)."""
    global _config
    _config = configs or {}


def _default_name() -> str:
    if _config:
        return _config.get("default") or "sqlite"
    return os.environ.get("DB_CONNECTION", "sqlite")


def connection(name: str | None = None) -> Engine:
    name = name or _default_name()
    if name not in _connections:
        _connections[name] = _make_engine(name)
    return _connections[name]


def _pool_kwargs() -> dict:
    """Read pool configuration from environment variables."""
    return {
        "pool_size": int(os.environ.get("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.environ.get("DB_MAX_OVERFLOW", "10")),
        "pool_timeout": int(os.environ.get("DB_POOL_TIMEOUT", "30")),
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE", "3600")),
    }


def _sqlite_engine(db_name: str) -> Engine:
    if db_name == ":memory:":
        return create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(f"sqlite:///{db_name}", connect_args={"check_same_thread": False})


def _server_engine(scheme: str, user: str, password: str, host: str, port: str, db: str) -> Engine:
    return create_engine(
        f"{scheme}://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}", **_pool_kwargs()
    )


def _make_engine(name: str) -> Engine:
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        # SQLite URLs must not use pooling kwargs
        if db_url.startswith("sqlite"):
            return create_engine(db_url, connect_args={"check_same_thread": False})
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
            return create_engine(url, connect_args={"check_same_thread": False})
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
