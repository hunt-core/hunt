from __future__ import annotations

from typing import Callable

from sqlalchemy import text

from hunt.database.connection import connection
from hunt.database.schema.blueprint import Blueprint


class Schema:
    _conn_name: str | None = None

    @classmethod
    def create(cls, table: str, callback: Callable[[Blueprint], None]) -> None:
        bp = Blueprint(table)
        callback(bp)
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            for sql in bp.to_create_sql():
                conn.execute(text(sql))
            conn.commit()

    @classmethod
    def table(cls, table: str, callback: Callable[[Blueprint], None]) -> None:
        """Alter an existing table."""
        bp = Blueprint(table)
        callback(bp)
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            for col in bp.columns:
                sql = f"ALTER TABLE {table} ADD COLUMN {Blueprint._column_sql(col)}"
                conn.execute(text(sql))
            for col_name in bp._drop_columns:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col_name}"))
            conn.commit()

    @classmethod
    def drop(cls, table: str) -> None:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"DROP TABLE {table}"))
            conn.commit()

    @classmethod
    def drop_if_exists(cls, table: str) -> None:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            conn.commit()

    @classmethod
    def has_table(cls, table: str) -> bool:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            )
            return result.fetchone() is not None

    @classmethod
    def has_column(cls, table: str, column: str) -> bool:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            return any(row[1] == column for row in result.fetchall())

    @classmethod
    def rename(cls, from_table: str, to_table: str) -> None:
        engine = connection(cls._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"ALTER TABLE {from_table} RENAME TO {to_table}"))
            conn.commit()
