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
from sqlalchemy.exc import IntegrityError, OperationalError
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
    @pytest.mark.parametrize(
        "col_type,expected",
        [
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
        ],
    )
    def test_postgresql_mapping(self, col_type, expected):
        assert Blueprint._resolve_type(col_type, "postgresql") == expected

    @pytest.mark.parametrize(
        "col_type",
        [
            "TINYINT",
            "MEDIUMTEXT",
            "LONGTEXT",
            "DATETIME",
            "DOUBLE",
            "BLOB",
            "FLOAT(8,2)",
        ],
    )
    def test_mysql_passthrough(self, col_type):
        assert Blueprint._resolve_type(col_type, "mysql") == col_type

    @pytest.mark.parametrize(
        "col_type",
        [
            "TINYINT",
            "MEDIUMTEXT",
            "LONGTEXT",
            "DATETIME",
            "DOUBLE",
            "BLOB",
            "FLOAT(8,2)",
        ],
    )
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
        with pytest.raises((IntegrityError, OperationalError)):
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


# ===========================================================================
# Database audit fixes (DB-01 .. DB-16, DB-L*, TEST-03) against real engines
# ===========================================================================

import importlib  # noqa: E402

from hunt.database.connection import begin as db_begin  # noqa: E402
from hunt.database.connection import raw as db_raw  # noqa: E402
from hunt.database.model import Model  # noqa: E402
from hunt.database.query_builder import QueryBuilder  # noqa: E402
from hunt.database.soft_deletes import SoftDeletes  # noqa: E402

_conn_mod = importlib.import_module("hunt.database.connection")

_FIX_TABLES = (
    "fx_users",
    "fx_posts",
    "fx_tags",
    "fx_post_tag",
    "fx_codes",
    "fx_children",
    "fx_parents",
    "fx_misc",
    "fx_pairs",
    "fx_partial",
    "fx_stray",
    "fx_mig_a",
)


def _drop_fix_tables(engine) -> None:
    dialect = engine.dialect.name
    with engine.connect() as c:
        if dialect == "sqlite":
            c.exec_driver_sql("PRAGMA foreign_keys=OFF")
        elif dialect == "mysql":
            c.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
        try:
            cascade = " CASCADE" if dialect == "postgresql" else ""
            for t in (*_FIX_TABLES, MIGRATIONS_TABLE):
                c.exec_driver_sql(f"DROP TABLE IF EXISTS {t}{cascade}")
            c.commit()
        finally:
            # The connection goes back to the pool: restore enforcement.
            if dialect == "sqlite":
                c.exec_driver_sql("PRAGMA foreign_keys=ON")
            elif dialect == "mysql":
                c.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
            c.commit()


@pytest.fixture
def orm(db, monkeypatch):
    """Make *db* the framework's default connection (ORM, Schema, raw, migrator)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(_conn_mod, "_config", {"default": "itest"})
    monkeypatch.setattr(_conn_mod, "_connections", {"itest": db})
    if db.dialect.name == "sqlite":
        # Same setup the framework applies to the SQLite engines it creates.
        _conn_mod._enable_sqlite_foreign_keys(db)
    _drop_fix_tables(db)
    Schema.create(
        "fx_users",
        lambda bp: (bp.id(), bp.string("name"), bp.string("email").nullable(), bp.integer("credits").default(0)),
    )
    Schema.create(
        "fx_posts",
        lambda bp: (
            bp.id(),
            bp.foreign_id("user_id").nullable().references("id").on("fx_users").on_delete("cascade"),
            bp.string("title"),
            bp.string("status").default("draft"),
            bp.timestamps(),
            bp.soft_deletes(),
        ),
    )
    yield db
    assert _conn_mod._pinned == {}
    _drop_fix_tables(db)
    if db.dialect.name == "sqlite":
        db.dispose()


class FxUser(Model):
    table = "fx_users"
    timestamps = False
    fillable = ["name", "email", "credits"]

    def posts(self):
        return self.has_many(FxPost, "user_id")


class FxPost(SoftDeletes, Model):
    table = "fx_posts"
    fillable = ["user_id", "title", "status"]

    def tags(self):
        return self.belongs_to_many(FxTag, "fx_post_tag", "fx_post_id", "fx_tag_id")


class FxTag(SoftDeletes, Model):
    table = "fx_tags"
    timestamps = False
    fillable = ["name"]


class FxCode(Model):
    table = "fx_codes"
    primary_key = "code"
    timestamps = False
    fillable = ["label"]


def _fresh_count(engine, table: str) -> int:
    with engine.connect() as c:
        return int(c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar())


class TestFixQueryBuilder:
    def test_where_like_escapes_wildcards(self, orm):
        FxUser.create(name="100% legit", email="a_b@example.com")
        FxUser.create(name="100 percent", email="aXb@example.com")
        FxUser.create(name="Bang! Smith", email="c@example.com")
        assert [u.name for u in FxUser.where_like("name", "100%").get()] == ["100% legit"]
        assert [u.name for u in FxUser.where_like("email", "a_b").get()] == ["100% legit"]
        assert [u.name for u in FxUser.where_like("name", "!").get()] == ["Bang! Smith"]
        assert FxUser.where_like("name", "%").count() == 1
        assert FxUser.where_like("name", "_").count() == 0
        assert FxUser.where_like("name", "").count() == 3
        # Case-insensitive on every dialect, and usable on a non-text column.
        assert FxUser.where_like("name", "bang").count() == 1
        assert FxUser.where_like("id", "1").or_where_like("name", "SMITH").count() == 2

    def test_empty_in_between_and_ilike(self, orm):
        for name in ("Alice", "bob", "Carol"):
            FxUser.create(name=name)
        assert FxUser.where_in("id", []).count() == 0
        assert FxUser.where_not_in("id", []).count() == 3
        assert FxUser.where("name", "x").or_where("id", "IN", [1, 2]).count() == 2
        ids = sorted(u.id for u in FxUser.all().get())
        assert FxUser.where("id", "BETWEEN", [ids[1], ids[2]]).count() == 2
        assert FxUser.where("name", "ILIKE", "ALICE").count() == 1
        assert FxUser.where("name", "NOT ILIKE", "ALICE").count() == 2
        assert FxUser.where("email", None).count() == 3
        assert FxUser.where("email", "!=", None).count() == 0

    def test_paging_inputs_are_clamped_and_offset_alone_is_valid(self, orm):
        for name in ("a", "b", "c"):
            FxUser.create(name=name)
        page = FxUser.order_by("id").paginate(per_page=0, page=-4)
        assert (page.per_page, page.current_page, page.total) == (1, 1, 3)
        assert [u.name for u in page.data] == ["a"]
        assert [u.name for u in FxUser.order_by("id").offset(1).get()] == ["b", "c"]
        with pytest.raises(ValueError):
            FxUser.query().offset(-1)

    def test_count_with_group_by_and_distinct(self, orm):
        u1 = FxUser.create(name="a")
        u2 = FxUser.create(name="b")
        for uid in (u1.id, u1.id, u2.id):
            FxPost.create(user_id=uid, title="t")
        assert FxPost.query().select("user_id").group_by("user_id").count() == 2
        assert FxPost.query().select("user_id").distinct().count() == 2
        page = QueryBuilder("fx_posts").select("user_id").group_by("user_id").order_by("user_id").paginate(1, 2)
        assert page.total == 2 and len(page.data) == 1

    def test_or_where_stays_inside_soft_delete_scope(self, orm):
        FxPost.create(title="a")
        FxPost.create(title="b").delete()
        assert [p.title for p in FxPost.where("title", "a").or_where("title", "b").get()] == ["a"]
        assert FxPost.where("title", "a").or_where("title", "b").update({"status": "x"}) == 1
        assert FxPost.query().only_trashed().first().status == "draft"
        assert FxPost.where("title", "zzz").or_where("title", "a").delete() == 1
        assert FxPost.query().with_trashed().count() == 2
        assert FxPost.query().with_trashed().where("title", "b").force_delete() == 1
        assert _fresh_count(orm, "fx_posts") == 1

    def test_insert_many_and_string_primary_key(self, orm):
        Schema.create("fx_codes", lambda bp: (bp.string("code", 32).primary_key(), bp.string("label").nullable()))
        QueryBuilder("fx_codes").insert_many([{"code": "A", "label": "la"}, {"label": "lb", "code": "B"}])
        assert FxCode.find("B").label == "lb"
        with pytest.raises(ValueError):
            QueryBuilder("fx_codes").insert_many([{"code": "C"}, {"code) VALUES ('x'); --": "D"}])
        code = FxCode().force_fill({"code": "X1", "label": "l"})
        code.save()
        assert code.code == "X1"
        code.update(label="changed")
        assert FxCode.find("X1").label == "changed"
        assert code.delete() is True and FxCode.find("X1") is None

    def test_generated_id_increment_and_json_free_roundtrip(self, orm):
        user = FxUser.create(name="a")
        assert isinstance(user.id, int) and user.id > 0
        assert user.increment("credits", 5) is True
        assert FxUser.query().where("id", user.id).decrement("credits", 2) == 1
        assert FxUser.find(user.id).credits == 3

    def test_eager_loading_relations(self, orm):
        Schema.create("fx_tags", lambda bp: (bp.id(), bp.string("name"), bp.soft_deletes()))
        Schema.create("fx_post_tag", lambda bp: (bp.integer("fx_post_id"), bp.integer("fx_tag_id")))
        user = FxUser.create(name="a")
        other = FxUser.create(name="b")
        post = FxPost.create(user_id=user.id, title="p", status="live")
        FxPost.create(user_id=other.id, title="q", status="live")
        live, gone = FxTag.create(name="live"), FxTag.create(name="gone")
        post.get_relation("tags").sync([live.id, gone.id])
        gone.delete()

        users = FxUser.where("id", user.id).with_(
            {"posts": lambda q: q.where("title", "zzz").or_where("status", "live")}
        ).get()
        assert [p.title for p in users[0]._relations["posts"]] == ["p"]
        posts = FxPost.where("id", post.id).with_("tags").get()
        assert [t.name for t in posts[0]._relations["tags"]] == ["live"]
        assert [t.name for t in post.tags()] == ["live"]
        with pytest.raises(ValueError):
            FxUser.with_("delete")


class TestFixTransactions:
    def test_rollback_and_savepoints(self, orm):
        with pytest.raises(RuntimeError):
            with db_begin():
                FxUser.create(name="rolled back")
                db_raw("INSERT INTO fx_users (name) VALUES ('raw')")
                assert FxUser.query().count() == 2
                raise RuntimeError("abort")
        assert _fresh_count(orm, "fx_users") == 0

        with db_begin():
            FxUser.create(name="outer")
            with pytest.raises(ValueError):
                with db_begin():
                    FxUser.create(name="inner")
                    raise ValueError("abort inner")
        assert [u.name for u in FxUser.all().get()] == ["outer"]
        if hasattr(orm.pool, "checkedout"):
            assert orm.pool.checkedout() == 0

    def test_uncommitted_orm_writes_are_invisible_elsewhere(self, orm):
        if orm.dialect.name == "sqlite":
            pytest.skip("the in-memory SQLite engine has a single shared connection")
        with db_begin():
            FxUser.create(name="a")
            assert _fresh_count(orm, "fx_users") == 0
        assert _fresh_count(orm, "fx_users") == 1

    def test_database_transactions_mixin(self, orm):
        from hunt.testing.test_case import DatabaseTransactions

        FxUser.create(name="before")
        case = DatabaseTransactions()
        case.setup_method()
        try:
            FxUser.create(name="during")
            with db_begin():
                FxUser.where("name", "before").update({"name": "changed"})
            assert FxUser.query().count() == 2
        finally:
            case.teardown_method()
        assert [u.name for u in FxUser.all().get()] == ["before"]

    def test_refresh_database_mixin_orders_by_foreign_keys(self, orm):
        from hunt.testing.test_case import RefreshDatabase

        user = FxUser.create(name="a")
        FxPost.create(user_id=user.id, title="t")

        class Case(RefreshDatabase):
            refresh_tables = ["fx_users", "fx_posts"]

        Case().teardown_method()
        assert _fresh_count(orm, "fx_users") == 0 and _fresh_count(orm, "fx_posts") == 0


class TestFixSchema:
    def test_id_and_foreign_id_are_compatible_and_enforced(self, orm):
        user = FxUser.create(name="a")
        FxPost.create(user_id=user.id, title="t")
        with pytest.raises(IntegrityError):
            QueryBuilder("fx_posts").insert({"user_id": 999_999, "title": "orphan"})
        # ON DELETE CASCADE declared through column.references().on().on_delete()
        FxUser.where("id", user.id).delete()
        assert _fresh_count(orm, "fx_posts") == 0

    def test_explicit_id_is_accepted(self, orm):
        # PostgreSQL: GENERATED BY DEFAULT (ALWAYS rejected explicit ids).
        QueryBuilder("fx_users").insert({"id": 5000, "name": "explicit"})
        assert FxUser.find(5000).name == "explicit"

    def test_timestamps_hold_values_past_2038(self, orm):
        big = 2**33
        QueryBuilder("fx_posts").insert({"title": "t", "created_at": big, "deleted_at": big})
        row = QueryBuilder("fx_posts").first()
        assert row["created_at"] == big and row["deleted_at"] == big

    def test_alter_adds_and_drops_indexes_and_foreign_keys(self, orm):
        Schema.create("fx_parents", lambda bp: (bp.id(), bp.string("name")))
        Schema.create("fx_children", lambda bp: (bp.id(), bp.foreign_id("parent_id"), bp.string("slug")))
        Schema.table(
            "fx_children",
            lambda bp: (
                bp.unique("slug"),
                bp.index(["parent_id", "slug"]),
                bp.foreign("parent_id").references("id").on("fx_parents").on_delete("cascade"),
            ),
        )
        db_raw("INSERT INTO fx_parents (name) VALUES ('p')")
        parent_id = db_raw("SELECT id FROM fx_parents").scalar()
        db_raw("INSERT INTO fx_children (parent_id, slug) VALUES (:p, 'a')", {"p": parent_id})
        with pytest.raises(IntegrityError):
            db_raw("INSERT INTO fx_children (parent_id, slug) VALUES (:p, 'a')", {"p": parent_id})
        with pytest.raises(IntegrityError):
            db_raw("INSERT INTO fx_children (parent_id, slug) VALUES (999999, 'b')")

        Schema.table(
            "fx_children",
            lambda bp: (bp.drop_foreign(["parent_id"]), bp.drop_unique(["slug"]), bp.drop_index(["parent_id", "slug"])),
        )
        db_raw("INSERT INTO fx_children (parent_id, slug) VALUES (999999, 'a')")
        assert _fresh_count(orm, "fx_children") == 2

    def test_new_column_with_reference_gets_a_foreign_key(self, orm):
        Schema.table(
            "fx_posts", lambda bp: bp.foreign_id("editor_id").nullable().references("id").on("fx_users")
        )
        with pytest.raises(IntegrityError):
            QueryBuilder("fx_posts").insert({"title": "t", "editor_id": 999_999})

    def test_composite_primary_key(self, orm):
        Schema.create(
            "fx_pairs", lambda bp: (bp.integer("a"), bp.integer("b"), bp.primary(["a", "b"]))
        )
        db_raw("INSERT INTO fx_pairs (a, b) VALUES (1, 1)")
        db_raw("INSERT INTO fx_pairs (a, b) VALUES (1, 2)")
        with pytest.raises(IntegrityError):
            db_raw("INSERT INTO fx_pairs (a, b) VALUES (1, 1)")

    def test_change_column_type_and_keep_constraints(self, orm):
        Schema.create(
            "fx_misc",
            lambda bp: (
                bp.id(),
                bp.string("amount"),
                bp.string("slug").unique(),
                bp.string("note").nullable(),
                bp.index("note"),
            ),
        )
        db_raw("INSERT INTO fx_misc (amount, slug) VALUES ('42', 'a')")

        def alter(bp):
            bp.integer("amount").default(7).change()  # VARCHAR -> INTEGER needs USING on PostgreSQL
            bp.datetime("note").nullable().change()  # DATETIME is not a PostgreSQL type
            bp.string("extra").nullable().after("amount")

        Schema.table("fx_misc", alter)
        assert db_raw("SELECT amount FROM fx_misc").scalar() == 42
        db_raw("INSERT INTO fx_misc (slug) VALUES ('b')")
        assert db_raw("SELECT amount FROM fx_misc WHERE slug = 'b'").scalar() == 7
        # The untouched UNIQUE constraint and the index survive (SQLite rebuild).
        with pytest.raises(IntegrityError):
            db_raw("INSERT INTO fx_misc (slug) VALUES ('a')")
        from sqlalchemy import inspect as sa_inspect

        index_names = {i["name"] for i in sa_inspect(orm).get_indexes("fx_misc")}
        assert "fx_misc_note_index" in index_names
        assert Schema.has_column("fx_misc", "extra")

    def test_defaults_with_colons_percents_quotes_and_backslashes(self, orm):
        value = "t: %H:%M :smile: it's a\\b"
        Schema.create("fx_misc", lambda bp: (bp.id(), bp.string("fmt").default(value), bp.string("x").nullable()))
        db_raw("INSERT INTO fx_misc (x) VALUES ('1')")
        assert db_raw("SELECT fmt FROM fx_misc").scalar() == value

    def test_drop_column_if_exists(self, orm):
        Schema.table("fx_users", lambda bp: bp.drop_column_if_exists("email", "no_such_column"))
        assert not Schema.has_column("fx_users", "email")


_FX_MIG_OK = """
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class CreateFxMigA(Migration):
    def up(self):
        Schema.create("fx_mig_a", lambda bp: (bp.id(), bp.string("name")))

    def down(self):
        Schema.drop_if_exists("fx_mig_a")
"""

_FX_MIG_FAIL = """
from hunt.database.connection import raw
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class Failing(Migration):
    def up(self):
        Schema.create("fx_partial", lambda bp: (bp.id(), bp.string("name")))
        raw("INSERT INTO fx_partial (name) VALUES ('seed')")
        raw("INSERT INTO fx_no_such_table (name) VALUES ('boom')")
"""


class TestFixMigrations:
    def test_failed_migration_is_atomic_or_reported_dirty(self, orm, tmp_path):
        from hunt.database.schema.migration import MigrationError

        (tmp_path / "0001_ok.py").write_text(_FX_MIG_OK)
        (tmp_path / "0002_fail.py").write_text(_FX_MIG_FAIL)
        m = Migrator(tmp_path)
        with pytest.raises(Exception, match="fx_no_such_table"):
            m.run()
        assert Schema.has_table("fx_mig_a")
        assert m._ran() == ["0001_ok"]
        if orm.dialect.name == "mysql":
            # No transactional DDL: the half-applied migration is flagged.
            assert m._dirty() == ["0002_fail"]
            with pytest.raises(MigrationError):
                m.run()
            assert {"migration": "0002_fail", "ran": False, "dirty": True} in m.status()
        else:
            assert not Schema.has_table("fx_partial")
            assert m._dirty() == []
            (tmp_path / "0002_fail.py").write_text(_FX_MIG_OK.replace("fx_mig_a", "fx_partial"))
            assert Migrator(tmp_path).run() == ["0002_fail"]

    def test_tracking_table_is_unique_and_reinitialisable(self, orm, tmp_path):
        Migrator(tmp_path)
        Migrator(tmp_path)
        db_raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('x', 1)")
        with pytest.raises(IntegrityError):
            db_raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('x', 2)")

    def test_fresh_drops_all_tables(self, orm, tmp_path):
        (tmp_path / "0001_ok.py").write_text(_FX_MIG_OK.replace('Schema.drop_if_exists("fx_mig_a")', "pass"))
        (tmp_path / "__init__.py").write_text("")
        m = Migrator(tmp_path)
        assert m.run() == ["0001_ok"]
        db_raw("INSERT INTO fx_mig_a (name) VALUES ('old')")
        Schema.create(
            "fx_stray", lambda bp: (bp.id(), bp.foreign_id("a_id").references("id").on("fx_mig_a"))
        )
        aid = db_raw("SELECT id FROM fx_mig_a").scalar()
        db_raw("INSERT INTO fx_stray (a_id) VALUES (:a)", {"a": aid})

        assert m.fresh() == ["0001_ok"]
        assert not Schema.has_table("fx_stray")
        assert not Schema.has_table("fx_users")
        assert _fresh_count(orm, "fx_mig_a") == 0
        assert m._ran() == ["0001_ok"]

    def test_rollback_missing_file(self, orm, tmp_path):
        from hunt.database.schema.migration import MigrationError

        (tmp_path / "0001_ok.py").write_text(_FX_MIG_OK)
        m = Migrator(tmp_path)
        m.run()
        (tmp_path / "0001_ok.py").unlink()
        with pytest.raises(MigrationError):
            m.rollback()
        assert m._ran() == ["0001_ok"]
        assert m.rollback(force=True) == ["0001_ok"]
