"""
Integration tests: schema builder dialect compatibility.

Runs each test against SQLite (in-memory), MySQL 8 (Docker), and PostgreSQL 15
(Docker). Requires the Docker daemon to be running.

Skip with:  SKIP_INTEGRATION=1 pytest tests/integration/
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from hunt.database.schema.blueprint import Blueprint
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import MIGRATIONS_TABLE, Migrator

if os.environ.get("SKIP_INTEGRATION") == "1":
    pytest.skip("SKIP_INTEGRATION=1", allow_module_level=True)

# Docker Desktop on Linux puts the socket somewhere non-standard.
if "DOCKER_HOST" not in os.environ:
    _desktop_sock = Path.home() / ".docker/desktop/docker.sock"
    if _desktop_sock.exists():
        os.environ["DOCKER_HOST"] = f"unix://{_desktop_sock}"


# ---------------------------------------------------------------------------
# Container fixtures  (session-scoped = one container for the whole run)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mysql_engine():
    from testcontainers.mysql import MySqlContainer

    c = MySqlContainer("mysql:8.0")
    c.start()
    try:
        url = c.get_connection_url()
        # Force pymysql driver regardless of what testcontainers returns.
        url = re.sub(r"^mysql(\+\w+)?://", "mysql+pymysql://", url)
        yield create_engine(url)
    finally:
        c.stop()


@pytest.fixture(scope="session")
def postgres_engine():
    from testcontainers.postgres import PostgresContainer

    c = PostgresContainer("postgres:15")
    c.start()
    try:
        yield create_engine(c.get_connection_url())
    finally:
        c.stop()


# ---------------------------------------------------------------------------
# Parametrised engine — lazy loading so SQLite tests never start containers
# ---------------------------------------------------------------------------

@pytest.fixture(params=["sqlite", "mysql", "postgres"])
def db(request):
    if request.param == "sqlite":
        return create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return request.getfixturevalue(f"{request.param}_engine")


# ---------------------------------------------------------------------------
# Connection patcher  (wires Hunt's internal connection() to the test engine)
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(db):
    with (
        patch("hunt.database.schema.builder.connection", return_value=db),
        patch("hunt.database.schema.migration.connection", return_value=db),
    ):
        yield db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def drop(engine, *tables: str) -> None:
    with engine.connect() as c:
        for t in reversed(tables):
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
        c.commit()


def insert_one(engine, table: str, **values) -> dict:
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    with engine.connect() as c:
        c.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)
        c.commit()
        row = c.execute(text(f"SELECT * FROM {table} LIMIT 1")).fetchone()
    return dict(row._mapping)


# ===========================================================================
# Auto-increment primary key  (the original bug)
# ===========================================================================

class TestAutoIncrementId:
    def test_sequential_ids_on_all_dialects(self, conn):
        drop(conn, "ai_ids")
        Schema.create("ai_ids", lambda bp: (bp.id(), bp.string("val")))
        with conn.connect() as c:
            c.execute(text("INSERT INTO ai_ids (val) VALUES ('a')"))
            c.execute(text("INSERT INTO ai_ids (val) VALUES ('b')"))
            c.commit()
            ids = [r[0] for r in c.execute(text("SELECT id FROM ai_ids ORDER BY id")).fetchall()]
        assert ids == [1, 2]
        drop(conn, "ai_ids")


# ===========================================================================
# Dialect-specific column types
# ===========================================================================

class TestColumnTypes:
    """
    Each test verifies that a previously-broken column type can be created
    and round-tripped on all three databases.
    """

    def test_tiny_integer(self, conn):
        # TINYINT → SMALLINT on PostgreSQL
        drop(conn, "ct_tiny")
        Schema.create("ct_tiny", lambda bp: (bp.id(), bp.tiny_integer("score")))
        row = insert_one(conn, "ct_tiny", score=42)
        assert row["score"] == 42
        drop(conn, "ct_tiny")

    def test_long_text(self, conn):
        # LONGTEXT → TEXT on PostgreSQL
        drop(conn, "ct_long")
        Schema.create("ct_long", lambda bp: (bp.id(), bp.long_text("body")))
        big = "A" * 5_000
        row = insert_one(conn, "ct_long", body=big)
        assert row["body"] == big
        drop(conn, "ct_long")

    def test_medium_text(self, conn):
        # MEDIUMTEXT → TEXT on PostgreSQL
        drop(conn, "ct_med")
        Schema.create("ct_med", lambda bp: (bp.id(), bp.medium_text("body")))
        row = insert_one(conn, "ct_med", body="medium text value")
        assert row["body"] == "medium text value"
        drop(conn, "ct_med")

    def test_datetime_column(self, conn):
        # DATETIME → TIMESTAMP on PostgreSQL
        drop(conn, "ct_dt")
        Schema.create("ct_dt", lambda bp: (bp.id(), bp.datetime("happened_at").nullable()))
        with conn.connect() as c:
            c.execute(text("INSERT INTO ct_dt (happened_at) VALUES ('2024-06-01 12:00:00')"))
            c.commit()
            row = c.execute(text("SELECT happened_at FROM ct_dt LIMIT 1")).fetchone()
        assert row[0] is not None
        drop(conn, "ct_dt")

    def test_double_column(self, conn):
        # DOUBLE → DOUBLE PRECISION on PostgreSQL
        drop(conn, "ct_dbl")
        Schema.create("ct_dbl", lambda bp: (bp.id(), bp.double("value")))
        row = insert_one(conn, "ct_dbl", value=3.141592653589793)
        assert abs(row["value"] - 3.141592653589793) < 1e-6
        drop(conn, "ct_dbl")

    def test_float_with_precision(self, conn):
        # FLOAT(p,s) → REAL on PostgreSQL (FLOAT only accepts one arg there)
        drop(conn, "ct_flt")
        Schema.create("ct_flt", lambda bp: (bp.id(), bp.float("amount", 8, 2)))
        row = insert_one(conn, "ct_flt", amount=9.99)
        assert abs(row["amount"] - 9.99) < 0.01
        drop(conn, "ct_flt")

    def test_float_high_precision_maps_to_double(self, conn):
        # FLOAT(p,s) where p > 24 → DOUBLE PRECISION on PostgreSQL
        drop(conn, "ct_flt2")
        Schema.create("ct_flt2", lambda bp: (bp.id(), bp.float("ratio", 53, 10)))
        row = insert_one(conn, "ct_flt2", ratio=1.23456789)
        assert abs(row["ratio"] - 1.23456789) < 1e-5
        drop(conn, "ct_flt2")

    def test_blob_column(self, conn):
        # BLOB → BYTEA on PostgreSQL
        drop(conn, "ct_blob")
        Schema.create("ct_blob", lambda bp: (bp.id(), bp.binary("data")))
        with conn.connect() as c:
            c.execute(text("INSERT INTO ct_blob (data) VALUES (:d)"), {"d": b"binary payload"})
            c.commit()
            raw = c.execute(text("SELECT data FROM ct_blob LIMIT 1")).fetchone()[0]
        # PostgreSQL returns memoryview; others return bytes
        value = bytes(raw) if not isinstance(raw, bytes) else raw
        assert value == b"binary payload"
        drop(conn, "ct_blob")

    def test_boolean_column(self, conn):
        drop(conn, "ct_bool")
        Schema.create("ct_bool", lambda bp: (bp.id(), bp.boolean("active")))
        row = insert_one(conn, "ct_bool", active=True)
        assert row["active"] in (True, 1)
        drop(conn, "ct_bool")

    def test_decimal_column(self, conn):
        drop(conn, "ct_dec")
        Schema.create("ct_dec", lambda bp: (bp.id(), bp.decimal("price", 10, 2)))
        row = insert_one(conn, "ct_dec", price=99.99)
        assert abs(float(row["price"]) - 99.99) < 0.001
        drop(conn, "ct_dec")

    def test_unsigned_integer_mysql_emits_unsigned_keyword(self, conn):
        # On MySQL: INTEGER UNSIGNED; on PostgreSQL/SQLite: plain INTEGER (no UNSIGNED)
        drop(conn, "ct_uint")
        Schema.create("ct_uint", lambda bp: (bp.id(), bp.unsigned_integer("qty")))
        row = insert_one(conn, "ct_uint", qty=100)
        assert row["qty"] == 100
        drop(conn, "ct_uint")

    def test_unsigned_big_integer(self, conn):
        drop(conn, "ct_ubig")
        Schema.create("ct_ubig", lambda bp: (bp.id(), bp.unsigned_big_integer("ref_id")))
        row = insert_one(conn, "ct_ubig", ref_id=9_999_999)
        assert row["ref_id"] == 9_999_999
        drop(conn, "ct_ubig")

    def test_foreign_id(self, conn):
        drop(conn, "ct_fk")
        Schema.create("ct_fk", lambda bp: (bp.id(), bp.foreign_id("owner_id")))
        row = insert_one(conn, "ct_fk", owner_id=7)
        assert row["owner_id"] == 7
        drop(conn, "ct_fk")


# ---------------------------------------------------------------------------
# Unit-level check: _resolve_type covers all mapped cases without a DB
# ---------------------------------------------------------------------------

class TestResolveType:
    @pytest.mark.parametrize("col_type,expected", [
        ("TINYINT", "SMALLINT"),
        ("MEDIUMTEXT", "TEXT"),
        ("LONGTEXT", "TEXT"),
        ("DATETIME", "TIMESTAMP"),
        ("DOUBLE", "DOUBLE PRECISION"),
        ("BLOB", "BYTEA"),
        ("FLOAT(8,2)", "REAL"),
        ("FLOAT(53,10)", "DOUBLE PRECISION"),
        # Passthrough types
        ("INTEGER", "INTEGER"),
        ("VARCHAR", "VARCHAR"),
        ("TEXT", "TEXT"),
        ("BOOLEAN", "BOOLEAN"),
    ])
    def test_postgresql_mapping(self, col_type, expected):
        assert Blueprint._resolve_type(col_type, "postgresql") == expected

    @pytest.mark.parametrize("col_type", [
        "TINYINT", "MEDIUMTEXT", "LONGTEXT", "DATETIME",
        "DOUBLE", "BLOB", "FLOAT(8,2)",
    ])
    def test_mysql_passthrough(self, col_type):
        assert Blueprint._resolve_type(col_type, "mysql") == col_type

    @pytest.mark.parametrize("col_type", [
        "TINYINT", "MEDIUMTEXT", "LONGTEXT", "DATETIME",
        "DOUBLE", "BLOB", "FLOAT(8,2)",
    ])
    def test_sqlite_passthrough(self, col_type):
        assert Blueprint._resolve_type(col_type, "sqlite") == col_type


class TestUnsignedKeyword:
    def test_emitted_on_mysql(self):
        bp = Blueprint("t")
        bp.unsigned_integer("qty")
        sql = Blueprint._column_sql(bp.columns[0], "mysql")
        assert "UNSIGNED" in sql

    def test_not_emitted_on_postgresql(self):
        bp = Blueprint("t")
        bp.unsigned_integer("qty")
        sql = Blueprint._column_sql(bp.columns[0], "postgresql")
        assert "UNSIGNED" not in sql

    def test_not_emitted_on_sqlite(self):
        bp = Blueprint("t")
        bp.unsigned_integer("qty")
        sql = Blueprint._column_sql(bp.columns[0], "sqlite")
        assert "UNSIGNED" not in sql


# ===========================================================================
# Constraints
# ===========================================================================

class TestConstraints:

    def test_nullable_accepts_null(self, conn):
        drop(conn, "tc_null")
        Schema.create("tc_null", lambda bp: (bp.id(), bp.string("note").nullable()))
        with conn.connect() as c:
            c.execute(text("INSERT INTO tc_null (note) VALUES (NULL)"))
            c.commit()
            row = c.execute(text("SELECT note FROM tc_null LIMIT 1")).fetchone()
        assert row[0] is None
        drop(conn, "tc_null")

    def test_not_null_rejects_null(self, conn):
        drop(conn, "tc_nn")
        Schema.create("tc_nn", lambda bp: (bp.id(), bp.string("name")))
        with pytest.raises(IntegrityError):
            with conn.connect() as c:
                c.execute(text("INSERT INTO tc_nn (name) VALUES (NULL)"))
                c.commit()
        drop(conn, "tc_nn")

    def test_unique_prevents_duplicates(self, conn):
        drop(conn, "tc_uniq")
        Schema.create("tc_uniq", lambda bp: (bp.id(), bp.string("email").unique()))
        with conn.connect() as c:
            c.execute(text("INSERT INTO tc_uniq (email) VALUES ('a@b.com')"))
            c.commit()
        with pytest.raises(IntegrityError):
            with conn.connect() as c:
                c.execute(text("INSERT INTO tc_uniq (email) VALUES ('a@b.com')"))
                c.commit()
        drop(conn, "tc_uniq")

    def test_default_value_applied(self, conn):
        drop(conn, "tc_def")
        Schema.create(
            "tc_def",
            lambda bp: (bp.id(), bp.string("name"), bp.integer("score").default(0)),
        )
        with conn.connect() as c:
            c.execute(text("INSERT INTO tc_def (name) VALUES ('alice')"))
            c.commit()
            row = c.execute(text("SELECT score FROM tc_def LIMIT 1")).fetchone()
        assert row[0] == 0
        drop(conn, "tc_def")

    def test_enum_check_accepts_valid(self, conn):
        drop(conn, "tc_enum")
        Schema.create("tc_enum", lambda bp: (bp.id(), bp.enum("status", ["draft", "pub"])))
        with conn.connect() as c:
            c.execute(text("INSERT INTO tc_enum (status) VALUES ('draft')"))
            c.commit()
            row = c.execute(text("SELECT status FROM tc_enum LIMIT 1")).fetchone()
        assert row[0] == "draft"
        drop(conn, "tc_enum")

    def test_enum_check_rejects_invalid(self, conn):
        drop(conn, "tc_enum2")
        Schema.create("tc_enum2", lambda bp: (bp.id(), bp.enum("status", ["draft", "pub"])))
        with pytest.raises(IntegrityError):
            with conn.connect() as c:
                c.execute(text("INSERT INTO tc_enum2 (status) VALUES ('bogus')"))
                c.commit()
        drop(conn, "tc_enum2")


# ===========================================================================
# Schema introspection
# ===========================================================================

class TestSchemaIntrospection:

    def test_has_table_true(self, conn):
        drop(conn, "si_ht")
        Schema.create("si_ht", lambda bp: bp.id())
        assert Schema.has_table("si_ht") is True
        drop(conn, "si_ht")

    def test_has_table_false(self, conn):
        drop(conn, "si_missing")
        assert Schema.has_table("si_missing") is False

    def test_has_column_true(self, conn):
        drop(conn, "si_hc")
        Schema.create("si_hc", lambda bp: (bp.id(), bp.string("email")))
        assert Schema.has_column("si_hc", "email") is True
        drop(conn, "si_hc")

    def test_has_column_false(self, conn):
        drop(conn, "si_hcf")
        Schema.create("si_hcf", lambda bp: bp.id())
        assert Schema.has_column("si_hcf", "ghost") is False
        drop(conn, "si_hcf")

    def test_drop_if_exists(self, conn):
        drop(conn, "si_drop")
        Schema.create("si_drop", lambda bp: bp.id())
        assert Schema.has_table("si_drop") is True
        Schema.drop_if_exists("si_drop")
        assert Schema.has_table("si_drop") is False

    def test_create_with_index(self, conn):
        drop(conn, "si_idx")
        Schema.create(
            "si_idx",
            lambda bp: (bp.id(), bp.string("slug"), bp.index("slug")),
        )
        assert Schema.has_table("si_idx") is True
        assert Schema.has_column("si_idx", "slug") is True
        drop(conn, "si_idx")

    def test_create_with_unique_index(self, conn):
        drop(conn, "si_uidx")
        Schema.create(
            "si_uidx",
            lambda bp: (bp.id(), bp.string("code"), bp.unique("code")),
        )
        with pytest.raises(IntegrityError):
            with conn.connect() as c:
                c.execute(text("INSERT INTO si_uidx (code) VALUES ('X')"))
                c.execute(text("INSERT INTO si_uidx (code) VALUES ('X')"))
                c.commit()
        drop(conn, "si_uidx")


# ===========================================================================
# Schema.table() — ALTER operations
# ===========================================================================

class TestAlterTable:

    def test_add_column(self, conn):
        drop(conn, "at_add")
        Schema.create("at_add", lambda bp: (bp.id(), bp.string("name")))
        Schema.table("at_add", lambda bp: bp.string("email").nullable())
        assert Schema.has_column("at_add", "email") is True
        drop(conn, "at_add")

    def test_rename_column(self, conn):
        drop(conn, "at_ren")
        Schema.create("at_ren", lambda bp: (bp.id(), bp.string("old_name")))
        Schema.table("at_ren", lambda bp: bp.rename_column("old_name", "new_name"))
        assert Schema.has_column("at_ren", "new_name") is True
        assert Schema.has_column("at_ren", "old_name") is False
        drop(conn, "at_ren")

    def test_drop_column(self, conn):
        drop(conn, "at_drop")
        Schema.create("at_drop", lambda bp: (bp.id(), bp.string("keep"), bp.string("gone")))
        Schema.table("at_drop", lambda bp: bp.drop_column("gone"))
        assert Schema.has_column("at_drop", "keep") is True
        assert Schema.has_column("at_drop", "gone") is False
        drop(conn, "at_drop")


# ===========================================================================
# Migrations
# ===========================================================================

_MIGRATION_CODE = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class CreateMigItems(Migration):
    def up(self) -> None:
        Schema.create("mig_items", lambda bp: (bp.id(), bp.string("label")))

    def down(self) -> None:
        Schema.drop_if_exists("mig_items")
"""


class TestMigrations:

    def test_creates_migrations_table(self, conn):
        drop(conn, MIGRATIONS_TABLE)
        with tempfile.TemporaryDirectory() as tmp:
            Migrator(Path(tmp))
        assert Schema.has_table(MIGRATIONS_TABLE)
        drop(conn, MIGRATIONS_TABLE)

    def test_run_and_rollback(self, conn):
        drop(conn, MIGRATIONS_TABLE, "mig_items")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "0001_create_items.py").write_text(_MIGRATION_CODE)
            m = Migrator(p)

            ran = m.run()
            assert "0001_create_items" in ran
            assert Schema.has_table("mig_items")

            rolled = m.rollback()
            assert "0001_create_items" in rolled
            assert not Schema.has_table("mig_items")
        drop(conn, MIGRATIONS_TABLE)

    def test_run_is_idempotent(self, conn):
        drop(conn, MIGRATIONS_TABLE, "mig_items")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "0001_create_items.py").write_text(_MIGRATION_CODE)
            m = Migrator(p)
            m.run()
            assert m.run() == []
        drop(conn, MIGRATIONS_TABLE, "mig_items")

    def test_status_reports_ran(self, conn):
        drop(conn, MIGRATIONS_TABLE, "mig_items")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "0001_create_items.py").write_text(_MIGRATION_CODE)
            m = Migrator(p)
            m.run()
            status = m.status()
        assert any(s["migration"] == "0001_create_items" and s["ran"] for s in status)
        drop(conn, MIGRATIONS_TABLE, "mig_items")
