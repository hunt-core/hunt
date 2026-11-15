"""Regression tests for the database audit findings: schema builder and migrations.

DB-11, DB-12, DB-13, DB-15, DB-L6, DB-L7, DB-L8, DB-L9, DB-L13.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from hunt.database.connection import begin, raw
from hunt.database.schema.blueprint import Blueprint
from hunt.database.schema.builder import Schema, _parse_create_table, _split_top_level
from hunt.database.schema.migration import MIGRATIONS_TABLE, MigrationError, Migrator

conn_mod = importlib.import_module("hunt.database.connection")
builder_mod = importlib.import_module("hunt.database.schema.builder")


def _reset(monkeypatch, database: str) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECTION", "sqlite")
    monkeypatch.setenv("DB_DATABASE", database)
    monkeypatch.setattr(conn_mod, "_config", {})
    monkeypatch.setattr(conn_mod, "_connections", {})


@pytest.fixture(params=["memory", "file"])
def db(request, monkeypatch, tmp_path):
    _reset(monkeypatch, ":memory:" if request.param == "memory" else str(tmp_path / "app.sqlite"))
    engine = conn_mod.connection()
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# A recording engine: captures the DDL Schema would send to MySQL / PostgreSQL.
# ---------------------------------------------------------------------------


class _Result:
    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def scalar(self):
        return None


class _RecordingConnection:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def exec_driver_sql(self, sql, parameters=None, execution_options=None):
        assert execution_options == {"no_parameters": True}
        self.statements.append(sql)
        return _Result()

    def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass


class _RecordingEngine:
    def __init__(self, dialect: str) -> None:
        self.dialect = type("Dialect", (), {"name": dialect})()
        self.statements: list[str] = []

    def connect(self):
        return _RecordingConnection(self.statements)


@pytest.fixture
def record(monkeypatch):
    def _record(dialect: str) -> list[str]:
        engine = _RecordingEngine(dialect)
        monkeypatch.setattr(builder_mod, "connection", lambda name=None: engine)
        return engine.statements

    return _record


# ---------------------------------------------------------------------------
# DB-11: Schema.table emits indexes, constraints and foreign keys
# ---------------------------------------------------------------------------


class TestAlterTableSqlGeneration:
    @staticmethod
    def _alter(bp: Blueprint) -> None:
        bp.string("slug").nullable()
        bp.unique("slug")
        bp.index(["user_id", "slug"])
        bp.foreign("user_id").references("id").on("users").on_delete("cascade")

    def test_mysql(self, record):
        statements = record("mysql")
        Schema.table("posts", self._alter)
        assert statements == [
            "ALTER TABLE posts ADD COLUMN slug VARCHAR(255)",
            "CREATE UNIQUE INDEX posts_slug_unique ON posts (slug)",
            "CREATE INDEX posts_user_id_slug_index ON posts (user_id, slug)",
            "ALTER TABLE posts ADD CONSTRAINT posts_user_id_foreign FOREIGN KEY (user_id)"
            " REFERENCES users(id) ON DELETE CASCADE",
        ]

    def test_postgresql(self, record):
        statements = record("postgresql")
        Schema.table("posts", self._alter)
        assert statements == [
            "ALTER TABLE posts ADD COLUMN slug VARCHAR(255)",
            "CREATE UNIQUE INDEX posts_slug_unique ON posts (slug)",
            "CREATE INDEX posts_user_id_slug_index ON posts (user_id, slug)",
            "ALTER TABLE posts ADD CONSTRAINT posts_user_id_foreign FOREIGN KEY (user_id)"
            " REFERENCES users(id) ON DELETE CASCADE",
        ]

    def test_new_column_with_references_adds_a_foreign_key(self, record):
        for dialect, col_type in (("mysql", "BIGINT UNSIGNED"), ("postgresql", "BIGINT")):
            statements = record(dialect)
            Schema.table("posts", lambda bp: bp.foreign_id("user_id").references("id").on("users"))
            assert statements == [
                f"ALTER TABLE posts ADD COLUMN user_id {col_type} NOT NULL",
                "ALTER TABLE posts ADD CONSTRAINT posts_user_id_foreign FOREIGN KEY (user_id) REFERENCES users(id)",
            ]

    def test_drop_index_unique_and_foreign(self, record):
        def alter(bp: Blueprint) -> None:
            bp.drop_index(["user_id", "slug"])
            bp.drop_unique(["slug"])
            bp.drop_index("custom_idx")
            bp.drop_foreign(["user_id"])
            bp.drop_foreign("legacy_fk")

        statements = record("mysql")
        Schema.table("posts", alter)
        assert statements == [
            "ALTER TABLE posts DROP FOREIGN KEY posts_user_id_foreign",
            "ALTER TABLE posts DROP FOREIGN KEY legacy_fk",
            "DROP INDEX posts_user_id_slug_index ON posts",
            "DROP INDEX posts_slug_unique ON posts",
            "DROP INDEX custom_idx ON posts",
        ]
        statements = record("postgresql")
        Schema.table("posts", alter)
        assert statements == [
            "ALTER TABLE posts DROP CONSTRAINT posts_user_id_foreign",
            "ALTER TABLE posts DROP CONSTRAINT legacy_fk",
            "DROP INDEX posts_user_id_slug_index",
            "DROP INDEX posts_slug_unique",
            "DROP INDEX custom_idx",
        ]

    def test_composite_primary_key_on_alter(self, record):
        for dialect in ("mysql", "postgresql"):
            statements = record(dialect)
            Schema.table("role_user", lambda bp: bp.primary(["role_id", "user_id"]))
            assert statements == ["ALTER TABLE role_user ADD PRIMARY KEY (role_id, user_id)"]

    def test_after_is_emitted_on_mysql_only(self, record):
        def alter(bp: Blueprint) -> None:
            bp.string("nick").nullable().after("name")
            bp.string("name", 100).after("id").change()

        statements = record("mysql")
        Schema.table("users", alter)
        assert statements == [
            "ALTER TABLE users ADD COLUMN nick VARCHAR(255) AFTER name",
            "ALTER TABLE users MODIFY COLUMN name VARCHAR(100) NOT NULL AFTER id",
        ]
        statements = record("postgresql")
        Schema.table("users", lambda bp: bp.string("nick").nullable().after("name"))
        assert statements == ["ALTER TABLE users ADD COLUMN nick VARCHAR(255)"]

    def test_invalid_identifiers_are_rejected(self):
        bp = Blueprint("posts")
        with pytest.raises(ValueError):
            bp.drop_index("x; DROP TABLE users")
        with pytest.raises(ValueError):
            bp.drop_foreign("x y")
        with pytest.raises(ValueError):
            bp.string("a").after("b; --")
        with pytest.raises(ValueError):
            bp.foreign("user_id").references("id").on("users").on_delete("drop everything")


class TestCreateTableSqlGeneration:
    def test_column_references_become_a_table_level_foreign_key(self):
        bp = Blueprint("posts")
        bp.id()
        bp.foreign_id("user_id").references("id").on("users").on_delete("cascade").on_update("restrict")
        for dialect in ("sqlite", "mysql", "postgresql"):
            create = bp.to_create_sql(dialect)[0]
            assert (
                "CONSTRAINT posts_user_id_foreign FOREIGN KEY (user_id) REFERENCES users(id)"
                " ON DELETE CASCADE ON UPDATE RESTRICT"
            ) in create

    def test_foreign_builder_and_column_reference_are_not_duplicated(self):
        bp = Blueprint("posts")
        bp.foreign_id("user_id").references("id").on("users")
        bp.foreign("user_id").references("id").on("users").name("custom_fk")
        create = bp.to_create_sql("postgresql")[0]
        assert create.count("FOREIGN KEY") == 1
        assert "CONSTRAINT custom_fk FOREIGN KEY (user_id)" in create

    def test_composite_primary_key_is_a_real_primary_key(self):
        bp = Blueprint("role_user")
        bp.integer("role_id")
        bp.integer("user_id")
        bp.primary(["role_id", "user_id"])
        for dialect in ("sqlite", "mysql", "postgresql"):
            stmts = bp.to_create_sql(dialect)
            assert "PRIMARY KEY (role_id, user_id)" in stmts[0]
            assert len(stmts) == 1  # no stray unique index

    def test_key_types_per_dialect(self):
        # DB-L7 / DB-L8
        bp = Blueprint("t")
        id_col = bp.id()
        fk_col = bp.foreign_id("user_id")
        assert Blueprint._column_sql(id_col, "mysql") == "id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT"
        assert Blueprint._column_sql(fk_col, "mysql") == "user_id BIGINT UNSIGNED NOT NULL"
        assert Blueprint._column_sql(id_col, "postgresql") == "id BIGINT PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY"
        assert Blueprint._column_sql(fk_col, "postgresql") == "user_id BIGINT NOT NULL"
        assert Blueprint._column_sql(id_col, "sqlite") == "id INTEGER PRIMARY KEY AUTOINCREMENT"
        assert Blueprint._column_sql(fk_col, "sqlite") == "user_id INTEGER NOT NULL"

    def test_timestamps_are_64_bit(self):
        # DB-L6
        bp = Blueprint("t")
        bp.timestamps()
        bp.soft_deletes()
        bp.timestamp("published_at")
        for dialect in ("sqlite", "mysql", "postgresql"):
            for col in bp.columns:
                assert Blueprint._column_sql(col, dialect).split()[1] == "BIGINT"

    def test_plain_integers_are_unchanged(self):
        bp = Blueprint("t")
        col = bp.integer("n")
        ucol = bp.unsigned_integer("u")
        assert Blueprint._column_sql(col, "mysql") == "n INTEGER NOT NULL"
        assert Blueprint._column_sql(ucol, "mysql") == "u INTEGER UNSIGNED NOT NULL"
        assert Blueprint._column_sql(ucol, "postgresql") == "u INTEGER NOT NULL"


# ---------------------------------------------------------------------------
# DB-15: PostgreSQL .change()
# ---------------------------------------------------------------------------


class TestPostgresChange:
    def test_types_are_mapped_and_cast(self, record):
        statements = record("postgresql")

        def alter(bp: Blueprint) -> None:
            bp.datetime("published_at").nullable().change()
            bp.tiny_integer("level").default(1).change()
            bp.string("code", 40).change()
            bp.double("score").change()

        Schema.table("posts", alter)
        assert statements == [
            "ALTER TABLE posts ALTER COLUMN published_at DROP DEFAULT",
            "ALTER TABLE posts ALTER COLUMN published_at TYPE TIMESTAMP USING published_at::TIMESTAMP",
            "ALTER TABLE posts ALTER COLUMN published_at DROP NOT NULL",
            "ALTER TABLE posts ALTER COLUMN level DROP DEFAULT",
            "ALTER TABLE posts ALTER COLUMN level TYPE SMALLINT USING level::SMALLINT",
            "ALTER TABLE posts ALTER COLUMN level SET NOT NULL",
            "ALTER TABLE posts ALTER COLUMN level SET DEFAULT 1",
            "ALTER TABLE posts ALTER COLUMN code DROP DEFAULT",
            "ALTER TABLE posts ALTER COLUMN code TYPE VARCHAR(40) USING code::VARCHAR(40)",
            "ALTER TABLE posts ALTER COLUMN code SET NOT NULL",
            "ALTER TABLE posts ALTER COLUMN score DROP DEFAULT",
            "ALTER TABLE posts ALTER COLUMN score TYPE DOUBLE PRECISION USING score::DOUBLE PRECISION",
            "ALTER TABLE posts ALTER COLUMN score SET NOT NULL",
        ]
        assert not any("DATETIME" in s or "TINYINT" in s for s in statements)


# ---------------------------------------------------------------------------
# DB-11 on SQLite (real database)
# ---------------------------------------------------------------------------


def _indexes(table: str) -> dict[str, bool]:
    rows = raw(f"PRAGMA index_list({table})").fetchall()
    return {r[1]: bool(r[2]) for r in rows if not str(r[1]).startswith("sqlite_autoindex")}


def _fks(table: str) -> list[tuple]:
    return [(r[2], r[3], r[4], r[6]) for r in raw(f"PRAGMA foreign_key_list({table})").fetchall()]


class TestAlterTableOnSqlite:
    def _base(self):
        Schema.create("s_users", lambda bp: (bp.id(), bp.string("name")))
        Schema.create(
            "s_posts",
            lambda bp: (bp.id(), bp.integer("user_id").nullable(), bp.string("slug"), bp.string("title")),
        )

    def test_unique_index_added_in_alter_is_enforced(self, db):
        self._base()
        Schema.table("s_posts", lambda bp: (bp.unique("slug"), bp.index(["user_id", "title"])))
        assert _indexes("s_posts") == {"s_posts_slug_unique": True, "s_posts_user_id_title_index": False}
        raw("INSERT INTO s_posts (slug, title) VALUES ('a', 't')")
        with pytest.raises(IntegrityError):
            raw("INSERT INTO s_posts (slug, title) VALUES ('a', 't2')")

    def test_drop_index(self, db):
        self._base()
        Schema.table("s_posts", lambda bp: (bp.unique("slug"), bp.index("title")))
        Schema.table("s_posts", lambda bp: (bp.drop_unique(["slug"]), bp.drop_index(["title"])))
        assert _indexes("s_posts") == {}

    def test_foreign_key_added_in_alter_is_enforced(self, db):
        self._base()
        raw("INSERT INTO s_users (name) VALUES ('u')")
        raw("INSERT INTO s_posts (user_id, slug, title) VALUES (1, 'a', 't')")
        Schema.table("s_posts", lambda bp: bp.foreign("user_id").references("id").on("s_users").on_delete("cascade"))
        assert _fks("s_posts") == [("s_users", "user_id", "id", "CASCADE")]
        assert raw("SELECT COUNT(*) FROM s_posts").scalar() == 1
        with pytest.raises(IntegrityError):
            raw("INSERT INTO s_posts (user_id, slug, title) VALUES (999, 'b', 't')")
        raw("DELETE FROM s_users WHERE id = 1")
        assert raw("SELECT COUNT(*) FROM s_posts").scalar() == 0

    def test_adding_a_foreign_key_that_existing_rows_violate_changes_nothing(self, db):
        self._base()
        raw("INSERT INTO s_posts (user_id, slug, title) VALUES (42, 'a', 't')")
        with pytest.raises(RuntimeError):
            Schema.table("s_posts", lambda bp: bp.foreign("user_id").references("id").on("s_users"))
        assert _fks("s_posts") == []
        assert raw("SELECT COUNT(*) FROM s_posts").scalar() == 1
        with db.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_new_nullable_column_with_reference_gets_an_inline_foreign_key(self, db):
        self._base()
        Schema.table("s_posts", lambda bp: bp.foreign_id("editor_id").nullable().references("id").on("s_users"))
        assert _fks("s_posts") == [("s_users", "editor_id", "id", "NO ACTION")]

    def test_drop_foreign(self, db):
        self._base()
        Schema.table("s_posts", lambda bp: bp.foreign("user_id").references("id").on("s_users"))
        Schema.table("s_posts", lambda bp: bp.drop_foreign(["user_id"]))
        assert _fks("s_posts") == []

    def test_create_with_column_reference_and_composite_primary_key(self, db):
        self._base()
        Schema.create(
            "s_likes",
            lambda bp: (
                bp.foreign_id("user_id").references("id").on("s_users"),
                bp.foreign_id("post_id").references("id").on("s_posts"),
                bp.primary(["user_id", "post_id"]),
            ),
        )
        assert sorted(_fks("s_likes")) == [
            ("s_posts", "post_id", "id", "NO ACTION"),
            ("s_users", "user_id", "id", "NO ACTION"),
        ]
        pk_cols = [r[1] for r in raw("PRAGMA table_info(s_likes)").fetchall() if r[5]]
        assert pk_cols == ["user_id", "post_id"]


# ---------------------------------------------------------------------------
# DB-12: SQLite rebuild keeps everything it does not change
# ---------------------------------------------------------------------------


class TestSqliteRebuild:
    def _base(self):
        Schema.create("r_users", lambda bp: (bp.id(), bp.string("name")))

        def posts(bp: Blueprint) -> None:
            bp.id()
            bp.foreign_id("user_id").references("id").on("r_users").on_delete("cascade")
            bp.string("slug").unique()
            bp.string("title")
            bp.enum("status", ["draft", "live"]).default("draft")
            bp.integer("views").default(0)
            bp.index(["user_id", "status"])
            bp.unique(["title", "user_id"])

        Schema.create("r_posts", posts)
        raw(
            "CREATE TRIGGER r_posts_touch AFTER UPDATE ON r_posts BEGIN"
            " UPDATE r_posts SET views = views + 1 WHERE id = NEW.id AND NEW.title != OLD.title; END"
        )
        raw("INSERT INTO r_users (name) VALUES ('u')")
        raw("INSERT INTO r_posts (user_id, slug, title) VALUES (1, 'a', 'first')")
        raw("INSERT INTO r_posts (user_id, slug, title) VALUES (1, 'b', 'second')")
        raw("DELETE FROM r_posts WHERE slug = 'b'")

    def test_indexes_constraints_foreign_keys_and_autoincrement_survive(self, db):
        self._base()
        before_indexes = _indexes("r_posts")
        before_fks = _fks("r_posts")
        before_triggers = raw("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='r_posts'").fetchall()

        Schema.table("r_posts", lambda bp: bp.string("title", 500).nullable().change())

        assert _indexes("r_posts") == before_indexes
        assert before_indexes == {"r_posts_user_id_status_index": False, "r_posts_title_user_id_unique": True}
        assert _fks("r_posts") == before_fks == [("r_users", "user_id", "id", "CASCADE")]
        assert (
            raw("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='r_posts'").fetchall()
            == before_triggers
        )
        sql = raw("SELECT sql FROM sqlite_master WHERE type='table' AND name='r_posts'").scalar()
        assert "id INTEGER PRIMARY KEY AUTOINCREMENT" in sql
        assert "title VARCHAR(500)" in sql
        assert "CHECK(status IN ('draft', 'live'))" in sql
        assert "slug VARCHAR(255) NOT NULL UNIQUE" in sql
        assert raw("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '_hunt_rebuild%'").scalar() == 0

        # Data survived, and each preserved constraint is still enforced.
        assert raw("SELECT title, views FROM r_posts").fetchall() == [("first", 0)]
        raw("INSERT INTO r_posts (user_id, slug, title) VALUES (1, 'c', NULL)")
        # AUTOINCREMENT: the id of the deleted row 2 is not reused.
        assert raw("SELECT id FROM r_posts WHERE slug = 'c'").scalar() == 3
        with pytest.raises(IntegrityError):
            raw("INSERT INTO r_posts (user_id, slug, title) VALUES (1, 'a', 'dup slug')")
        with pytest.raises(IntegrityError):
            raw("INSERT INTO r_posts (user_id, slug, title, status) VALUES (1, 'd', 't', 'bogus')")
        with pytest.raises(IntegrityError):
            raw("INSERT INTO r_posts (user_id, slug, title) VALUES (999, 'e', 't')")
        raw("UPDATE r_posts SET title = 'renamed' WHERE slug = 'a'")
        assert raw("SELECT views FROM r_posts WHERE slug = 'a'").scalar() == 1
        with db.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_rebuilding_a_referenced_table_keeps_child_rows(self, db):
        self._base()
        Schema.table("r_users", lambda bp: bp.string("name", 50).nullable().change())
        assert raw("SELECT COUNT(*) FROM r_posts").scalar() == 1
        assert _fks("r_posts") == [("r_users", "user_id", "id", "CASCADE")]
        assert raw("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(IntegrityError):
            raw("INSERT INTO r_posts (user_id, slug, title) VALUES (999, 'z', 't')")

    def test_rebuild_with_a_view_on_the_table(self, db):
        self._base()
        raw("CREATE VIEW r_live AS SELECT id, title FROM r_posts WHERE status = 'live'")
        Schema.table("r_posts", lambda bp: bp.string("title", 500).change())
        assert raw("SELECT COUNT(*) FROM r_live").scalar() == 0

    def test_failed_rebuild_changes_nothing(self, db):
        self._base()
        before = raw("SELECT sql FROM sqlite_master WHERE name='r_posts'").scalar()
        with pytest.raises(RuntimeError):
            Schema.table("r_posts", lambda bp: bp.string("no_such_column").change())
        assert raw("SELECT sql FROM sqlite_master WHERE name='r_posts'").scalar() == before
        assert raw("SELECT COUNT(*) FROM r_posts").scalar() == 1

    def test_rebuild_of_referenced_table_inside_a_transaction_is_refused(self, db):
        self._base()
        with pytest.raises(RuntimeError, match="referenced by r_posts"):
            with begin():
                Schema.table("r_users", lambda bp: bp.string("name", 50).change())
        assert raw("SELECT COUNT(*) FROM r_posts").scalar() == 1

    def test_rebuild_of_unreferenced_table_inside_a_transaction_rolls_back(self, db):
        self._base()
        before = raw("SELECT sql FROM sqlite_master WHERE name='r_posts'").scalar()
        with pytest.raises(ValueError):
            with begin():
                Schema.table("r_posts", lambda bp: bp.string("title", 9).change())
                raise ValueError("abort")
        assert raw("SELECT sql FROM sqlite_master WHERE name='r_posts'").scalar() == before

    def test_create_table_parser(self):
        defs, suffix = _parse_create_table(
            "CREATE TABLE \"t\" (id INTEGER PRIMARY KEY, name VARCHAR(10) DEFAULT 'a,b)', "
            "CHECK (id IN (1, 2)), CONSTRAINT fk FOREIGN KEY (id) REFERENCES o(id)) WITHOUT ROWID"
        )
        assert defs == [
            "id INTEGER PRIMARY KEY",
            "name VARCHAR(10) DEFAULT 'a,b)'",
            "CHECK (id IN (1, 2))",
            "CONSTRAINT fk FOREIGN KEY (id) REFERENCES o(id)",
        ]
        assert suffix == "WITHOUT ROWID"
        assert _split_top_level("a, b(1, 2), 'x,y'") == ["a", "b(1, 2)", "'x,y'"]


# ---------------------------------------------------------------------------
# DB-L9 / DB-L13
# ---------------------------------------------------------------------------


class TestDdlExecution:
    def test_colon_word_and_percent_in_a_default(self, db):
        Schema.create(
            "d_notes",
            lambda bp: (bp.id(), bp.string("fmt").default("time: %H:%M :smile:"), bp.string("x").nullable()),
        )
        raw("INSERT INTO d_notes (x) VALUES ('1')")
        assert raw("SELECT fmt FROM d_notes").scalar() == "time: %H:%M :smile:"
        Schema.table("d_notes", lambda bp: bp.string("tag").default("a:b"))
        assert raw("SELECT tag FROM d_notes").scalar() == "a:b"

    def test_ddl_is_sent_without_bind_processing(self, record):
        statements = record("postgresql")
        Schema.create("d_notes", lambda bp: (bp.id(), bp.string("fmt").default("100% :sure")))
        assert "DEFAULT '100% :sure'" in statements[0]

    def test_mysql_default_escapes_backslashes(self):
        from hunt.database.schema.builder import _safe_default

        assert _safe_default("a\\", "mysql") == "'a\\\\'"
        assert _safe_default("it's", "mysql") == "'it''s'"
        assert _safe_default("a\\", "postgresql") == "'a\\'"

    def test_has_table_and_has_column_filter_by_schema_on_postgresql(self, record):
        statements = record("postgresql")
        Schema.has_table("users")
        Schema.has_column("users", "email")
        assert all("table_schema=current_schema()" in s for s in statements)
        assert len(statements) == 2

    def test_schema_on_selects_the_connection(self, monkeypatch):
        seen: list[object] = []
        engine = _RecordingEngine("postgresql")

        def fake_connection(name=None):
            seen.append(name)
            return engine

        monkeypatch.setattr(builder_mod, "connection", fake_connection)
        with Schema.on("analytics"):
            Schema.drop_if_exists("events")
        Schema.drop_if_exists("events")
        assert seen == ["analytics", None]


# ---------------------------------------------------------------------------
# DB-13: migration runner
# ---------------------------------------------------------------------------

_CREATE = """
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class Create{cls}(Migration):
    def up(self):
        Schema.create("{table}", lambda bp: (bp.id(), bp.string("name")))

    def down(self):
        Schema.drop_if_exists("{table}")
"""

_FAILING = """
from hunt.database.connection import raw
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class Failing(Migration):
    def up(self):
        Schema.create("mig_partial", lambda bp: (bp.id(), bp.string("name")))
        raw("INSERT INTO mig_partial (name) VALUES ('seed')")
        Schema.table("mig_no_such_table", lambda bp: bp.string("x"))
"""


def _write(path: Path, name: str, body: str) -> None:
    (path / f"{name}.py").write_text(body)


def _tables() -> set[str]:
    rows = raw("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {r[0] for r in rows}


class TestMigrator:
    def test_run_and_status(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        _write(mig, "0002_create_b", _CREATE.format(cls="B", table="mig_b"))
        m = Migrator(mig)
        assert m.status() == [
            {"migration": "0001_create_a", "ran": False},
            {"migration": "0002_create_b", "ran": False},
        ]
        assert m.run() == ["0001_create_a", "0002_create_b"]
        assert m.run() == []
        assert {"mig_a", "mig_b"} <= _tables()
        assert all(s["ran"] for s in m.status())

    def test_failed_migration_leaves_no_partial_schema_and_can_be_rerun(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        _write(mig, "0002_failing", _FAILING)
        m = Migrator(mig)
        with pytest.raises(Exception, match="mig_no_such_table"):
            m.run()
        # The first migration committed; the failed one left nothing behind.
        assert "mig_a" in _tables()
        assert "mig_partial" not in _tables()
        assert m._ran() == ["0001_create_a"]
        with db.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

        _write(mig, "0002_failing", _CREATE.format(cls="Fixed", table="mig_partial"))
        assert Migrator(mig).run() == ["0002_failing"]
        assert "mig_partial" in _tables()

    def test_files_starting_with_underscore_are_skipped(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        (mig / "__init__.py").write_text("")
        (mig / "_helpers.py").write_text("VALUE = 1\n")
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        m = Migrator(mig)
        assert m.run() == ["0001_create_a"]
        assert [s["migration"] for s in m.status()] == ["0001_create_a"]

    def test_tracking_table_has_a_unique_index(self, db, tmp_path):
        m = Migrator(tmp_path)
        raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('x', 1)")
        with pytest.raises(IntegrityError):
            raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('x', 2)")
        # Creating a second Migrator is harmless.
        Migrator(tmp_path)
        assert m._ran() == ["x"]

    def test_unique_index_is_added_to_a_legacy_tracking_table(self, db, tmp_path):
        raw(
            f"CREATE TABLE {MIGRATIONS_TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, migration VARCHAR(255), batch INTEGER)"
        )
        raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('old', 1)")
        Migrator(tmp_path)
        with pytest.raises(IntegrityError):
            raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('old', 2)")

    def test_fresh_drops_every_table(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(
            mig,
            "0001_create_a",
            _CREATE.format(cls="A", table="mig_a").replace(
                """    def down(self):
        Schema.drop_if_exists("mig_a")
""",
                "",
            ),
        )
        m = Migrator(mig)
        m.run()
        raw("INSERT INTO mig_a (name) VALUES ('old data')")
        # A table no migration knows about, with a foreign key to one that does.
        raw("CREATE TABLE mig_stray (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES mig_a(id))")
        raw("INSERT INTO mig_stray (a_id) VALUES (1)")

        assert m.fresh() == ["0001_create_a"]
        assert "mig_stray" not in _tables()
        assert raw("SELECT COUNT(*) FROM mig_a").scalar() == 0
        assert m._ran() == ["0001_create_a"]
        with db.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_rollback_runs_down_and_removes_tracking(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        m = Migrator(mig)
        m.run()
        _write(mig, "0002_create_b", _CREATE.format(cls="B", table="mig_b"))
        m.run()
        assert m.rollback() == ["0002_create_b"]
        assert "mig_b" not in _tables() and "mig_a" in _tables()
        assert m._ran() == ["0001_create_a"]

    def test_rollback_with_missing_file_raises_unless_forced(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        m = Migrator(mig)
        m.run()
        (mig / "0001_create_a.py").unlink()
        with pytest.raises(MigrationError, match="0001_create_a"):
            m.rollback()
        assert m._ran() == ["0001_create_a"]
        assert m.rollback(force=True) == ["0001_create_a"]
        assert m._ran() == []

    def test_failed_down_keeps_the_tracking_row(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        body = _CREATE.format(cls="A", table="mig_a").replace(
            'Schema.drop_if_exists("mig_a")', 'Schema.drop_if_exists("mig_a"); raise RuntimeError("down failed")'
        )
        _write(mig, "0001_create_a", body)
        m = Migrator(mig)
        m.run()
        with pytest.raises(RuntimeError, match="down failed"):
            m.rollback()
        assert m._ran() == ["0001_create_a"]
        assert "mig_a" in _tables()

    def test_dirty_row_blocks_run_and_shows_in_status(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        m = Migrator(mig)
        raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('0001_create_a', NULL)")
        with pytest.raises(MigrationError, match="did not finish"):
            m.run()
        assert m.status() == [{"migration": "0001_create_a", "ran": False, "dirty": True}]

    def test_non_transactional_migration_is_marked_dirty_on_failure(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(
            mig,
            "0001_failing",
            _FAILING.replace("class Failing(Migration):", "class Failing(Migration):\n    transactional = False"),
        )
        m = Migrator(mig)
        with pytest.raises(Exception, match="mig_no_such_table"):
            m.run()
        assert m._dirty() == ["0001_failing"]
        assert "mig_partial" in _tables()

    def test_conn_name_is_used_for_tracking_and_schema(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(conn_mod, "_connections", {})
        monkeypatch.setattr(
            conn_mod,
            "_config",
            {
                "default": "main",
                "connections": {
                    "main": {"driver": "sqlite", "database": str(tmp_path / "main.sqlite")},
                    "other": {"driver": "sqlite", "database": str(tmp_path / "other.sqlite")},
                },
            },
        )
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        try:
            assert Migrator(mig, conn_name="other").run() == ["0001_create_a"]

            def names(conn_name):
                rows = raw("SELECT name FROM sqlite_master WHERE type='table'", conn_name=conn_name).fetchall()
                return {r[0] for r in rows if not r[0].startswith("sqlite_")}

            assert names("other") == {MIGRATIONS_TABLE, "mig_a"}
            assert names("main") == set()
        finally:
            for engine in conn_mod._connections.values():
                engine.dispose()

    def test_load_prefers_the_class_defined_in_the_file(self, tmp_path):
        (tmp_path / "0001_x.py").write_text(
            "from hunt.database.schema.migration import Migration\n"
            "from hunt.database.schema.migration import Migration as AAA_Imported\n\n\n"
            "class AaaBase(Migration):\n    pass\n\n\n"
            "class ZzzReal(AaaBase):\n    def up(self):\n        pass\n"
        )
        assert type(Migrator._load(tmp_path / "0001_x.py")).__name__ == "ZzzReal"

        (tmp_path / "shared_base_for_fix_db.py").write_text(
            "from hunt.database.schema.migration import Migration\n\n\nclass AAAShared(Migration):\n    pass\n"
        )
        import sys

        sys.path.insert(0, str(tmp_path))
        try:
            (tmp_path / "0002_y.py").write_text(
                "from shared_base_for_fix_db import AAAShared\n\n\nclass Real(AAAShared):\n    def up(self):\n        pass\n"
            )
            assert type(Migrator._load(tmp_path / "0002_y.py")).__name__ == "Real"
        finally:
            sys.path.remove(str(tmp_path))
            sys.modules.pop("shared_base_for_fix_db", None)

    def test_migration_can_rebuild_a_referenced_table(self, db, tmp_path):
        mig = tmp_path / "migrations"
        mig.mkdir()
        _write(
            mig,
            "0001_base",
            """
from hunt.database.connection import raw
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class Base(Migration):
    def up(self):
        Schema.create("mig_parents", lambda bp: (bp.id(), bp.string("name")))
        Schema.create(
            "mig_children",
            lambda bp: (bp.id(), bp.foreign_id("parent_id").references("id").on("mig_parents")),
        )
        raw("INSERT INTO mig_parents (name) VALUES ('p')")
        raw("INSERT INTO mig_children (parent_id) VALUES (1)")
""",
        )
        _write(
            mig,
            "0002_change",
            """
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class Change(Migration):
    def up(self):
        Schema.table("mig_parents", lambda bp: bp.string("name", 50).nullable().change())
""",
        )
        assert Migrator(mig).run() == ["0001_base", "0002_change"]
        assert raw("SELECT COUNT(*) FROM mig_children").scalar() == 1
        assert raw("PRAGMA foreign_key_check").fetchall() == []


class TestMigrateCli:
    def _project(self, monkeypatch, tmp_path) -> Path:
        _reset(monkeypatch, str(tmp_path / "app.sqlite"))
        mig = tmp_path / "database" / "migrations"
        mig.mkdir(parents=True)
        _write(mig, "0001_create_a", _CREATE.format(cls="A", table="mig_a"))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.path", list(__import__("sys").path))
        return mig

    def test_fresh_and_rollback_refuse_in_production(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.migrate import migrate_group

        self._project(monkeypatch, tmp_path)
        runner = CliRunner()
        assert runner.invoke(migrate_group, []).exit_code == 0
        raw("INSERT INTO mig_a (name) VALUES ('keep me')")
        monkeypatch.setenv("APP_ENV", "production")

        result = runner.invoke(migrate_group, ["fresh", "--yes"])
        assert result.exit_code == 1 and "production" in result.output
        result = runner.invoke(migrate_group, ["rollback"])
        assert result.exit_code == 1 and "production" in result.output
        assert raw("SELECT COUNT(*) FROM mig_a").scalar() == 1

        result = runner.invoke(migrate_group, ["fresh", "--yes", "--force"])
        assert result.exit_code == 0, result.output
        assert raw("SELECT COUNT(*) FROM mig_a").scalar() == 0
        result = runner.invoke(migrate_group, ["rollback", "--force"])
        assert result.exit_code == 0 and "Rolled back: 0001_create_a" in result.output
        conn_mod.connection().dispose()

    def test_rollback_missing_file_reports_an_error(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.migrate import migrate_group

        mig = self._project(monkeypatch, tmp_path)
        # An unset APP_ENV means production (the framework default), where
        # rollback needs --force for a different reason; test a local app.
        monkeypatch.setenv("APP_ENV", "local")
        runner = CliRunner()
        runner.invoke(migrate_group, [])
        (mig / "0001_create_a.py").unlink()
        result = runner.invoke(migrate_group, ["rollback"])
        assert result.exit_code == 1 and "file is missing" in result.output
        result = runner.invoke(migrate_group, ["rollback", "--force"])
        assert result.exit_code == 0
        conn_mod.connection().dispose()

    def test_migrate_uses_the_configured_database_not_memory(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.migrate import migrate_group

        self._project(monkeypatch, tmp_path)
        result = CliRunner().invoke(migrate_group, [])
        assert result.exit_code == 0 and "in-memory" not in result.output
        assert (tmp_path / "app.sqlite").exists()
        conn_mod.connection().dispose()
