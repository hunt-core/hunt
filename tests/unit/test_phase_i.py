"""Phase I tests: Schema & Migration Gaps."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from hunt.database.schema.blueprint import Blueprint, ColumnDef
from hunt.database.schema.builder import Schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_memory_engine():
    return create_engine("sqlite:///:memory:")


def _patch_connection(engine, monkeypatch):
    """Route Schema's connection() to the given engine."""
    monkeypatch.setattr("hunt.database.schema.builder.connection", lambda name=None: engine)


def _columns_of(conn, table: str) -> dict[str, dict]:
    """Return {col_name: {type, notnull, pk, dflt_value}} from PRAGMA table_info."""
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return {
        row[1]: {"type": row[2], "notnull": row[3], "dflt_value": row[4], "pk": row[5]}
        for row in result.fetchall()
    }


# ===========================================================================
# ColumnDef — new modifiers
# ===========================================================================

class TestColumnDefModifiers:
    def test_change_sets_flag(self):
        col = ColumnDef("name", "VARCHAR", length=100)
        col.change()
        assert col.is_change is True

    def test_change_returns_self(self):
        col = ColumnDef("name", "VARCHAR")
        assert col.change() is col

    def test_after_stores_column(self):
        col = ColumnDef("name", "VARCHAR")
        col.after("created_at")
        assert col.after_column == "created_at"

    def test_after_returns_self(self):
        col = ColumnDef("name", "VARCHAR")
        assert col.after("id") is col

    def test_change_fluent_chain(self):
        col = ColumnDef("name", "VARCHAR", length=200)
        result = col.nullable().change()
        assert result is col
        assert col.is_nullable is True
        assert col.is_change is True


# ===========================================================================
# enum() column type
# ===========================================================================

class TestEnumColumn:
    def test_blueprint_enum_adds_column(self):
        bp = Blueprint("orders")
        bp.enum("status", ["pending", "active", "closed"])
        assert len(bp.columns) == 1
        col = bp.columns[0]
        assert col.name == "status"
        assert col.enum_values == ["pending", "active", "closed"]

    def test_enum_sql_has_check_constraint(self):
        bp = Blueprint("orders")
        bp.enum("status", ["pending", "active"])
        sql = Blueprint._column_sql(bp.columns[0])
        assert "CHECK" in sql
        assert "'pending'" in sql
        assert "'active'" in sql
        assert "status IN" in sql

    def test_enum_sql_not_null_by_default(self):
        bp = Blueprint("orders")
        bp.enum("status", ["a", "b"])
        sql = Blueprint._column_sql(bp.columns[0])
        assert "NOT NULL" in sql

    def test_enum_nullable(self):
        bp = Blueprint("orders")
        bp.enum("status", ["a", "b"]).nullable()
        sql = Blueprint._column_sql(bp.columns[0])
        assert "NOT NULL" not in sql
        assert "CHECK" in sql

    def test_enum_creates_table_with_check(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)
        Schema.create("products", lambda t: t.enum("status", ["draft", "published"]))
        with engine.connect() as conn:
            # Valid insert succeeds
            conn.execute(text("INSERT INTO products (status) VALUES ('draft')"))
            conn.commit()
            # Invalid insert raises
            with pytest.raises(Exception):
                conn.execute(text("INSERT INTO products (status) VALUES ('invalid')"))
                conn.commit()

    def test_enum_default(self):
        bp = Blueprint("orders")
        bp.enum("status", ["a", "b"]).default("a")
        sql = Blueprint._column_sql(bp.columns[0])
        assert "DEFAULT 'a'" in sql
        assert "CHECK" in sql


# ===========================================================================
# morphs() / nullable_morphs()
# ===========================================================================

class TestMorphs:
    def test_morphs_adds_two_columns(self):
        bp = Blueprint("comments")
        bp.morphs("commentable")
        assert len(bp.columns) == 2
        names = [c.name for c in bp.columns]
        assert "commentable_id" in names
        assert "commentable_type" in names

    def test_morphs_id_is_bigint_unsigned(self):
        bp = Blueprint("comments")
        bp.morphs("commentable")
        id_col = next(c for c in bp.columns if c.name == "commentable_id")
        assert "BIGINT" in id_col.type
        assert id_col.unsigned is True

    def test_morphs_type_is_varchar(self):
        bp = Blueprint("comments")
        bp.morphs("commentable")
        type_col = next(c for c in bp.columns if c.name == "commentable_type")
        assert "VARCHAR" in type_col.type

    def test_morphs_not_nullable_by_default(self):
        bp = Blueprint("comments")
        bp.morphs("commentable")
        for col in bp.columns:
            assert col.is_nullable is False

    def test_morphs_adds_index(self):
        bp = Blueprint("comments")
        bp.morphs("commentable")
        assert len(bp.indexes) == 1
        idx = bp.indexes[0]
        assert "commentable_id" in idx.columns
        assert "commentable_type" in idx.columns

    def test_nullable_morphs_are_nullable(self):
        bp = Blueprint("likes")
        bp.nullable_morphs("likeable")
        for col in bp.columns:
            assert col.is_nullable is True

    def test_nullable_morphs_adds_index(self):
        bp = Blueprint("likes")
        bp.nullable_morphs("likeable")
        assert len(bp.indexes) == 1

    def test_morphs_creates_table(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)
        Schema.create("comments", lambda t: (t.id(), t.morphs("commentable"), t.text("body")))
        with engine.connect() as conn:
            cols = _columns_of(conn, "comments")
        assert "commentable_id" in cols
        assert "commentable_type" in cols

    def test_nullable_morphs_creates_table(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)
        Schema.create("reactions", lambda t: (t.id(), t.nullable_morphs("reactable")))
        with engine.connect() as conn:
            cols = _columns_of(conn, "reactions")
        # nullable: notnull == 0
        assert cols["reactable_id"]["notnull"] == 0
        assert cols["reactable_type"]["notnull"] == 0


# ===========================================================================
# change() — ALTER COLUMN (SQLite rebuild)
# ===========================================================================

class TestChangeColumn:
    def test_change_column_type_sqlite(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        # Create table with VARCHAR(50) name
        Schema.create("users", lambda t: (t.id(), t.string("name", 50), t.string("email")))

        # Change name to VARCHAR(255)
        def alter(t):
            t.string("name", 255).change()

        Schema.table("users", alter)

        with engine.connect() as conn:
            cols = _columns_of(conn, "users")
        assert "name" in cols
        # Original data structure preserved (table exists and has the column)

    def test_change_column_adds_nullable(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("posts", lambda t: (t.id(), t.string("bio")))

        # Insert a row
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO posts (bio) VALUES ('hello')"))
            conn.commit()

        # Change bio to nullable
        Schema.table("posts", lambda t: t.string("bio", 255).nullable().change())

        # Data preserved after rebuild
        with engine.connect() as conn:
            row = conn.execute(text("SELECT bio FROM posts")).fetchone()
        assert row[0] == "hello"

    def test_change_column_preserves_other_columns(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("items", lambda t: (t.id(), t.string("name"), t.integer("qty")))

        with engine.connect() as conn:
            conn.execute(text("INSERT INTO items (name, qty) VALUES ('widget', 5)"))
            conn.commit()

        Schema.table("items", lambda t: t.string("name", 500).change())

        with engine.connect() as conn:
            row = conn.execute(text("SELECT name, qty FROM items")).fetchone()
        assert row[0] == "widget"
        assert row[1] == 5


# ===========================================================================
# rename_column
# ===========================================================================

class TestRenameColumn:
    def test_rename_column_via_blueprint(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("articles", lambda t: (t.id(), t.string("title")))

        with engine.connect() as conn:
            conn.execute(text("INSERT INTO articles (title) VALUES ('Hello')"))
            conn.commit()

        Schema.table("articles", lambda t: t.rename_column("title", "headline"))

        with engine.connect() as conn:
            cols = _columns_of(conn, "articles")
        assert "headline" in cols
        assert "title" not in cols

    def test_renamed_column_preserves_data(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("notes", lambda t: (t.id(), t.text("body")))

        with engine.connect() as conn:
            conn.execute(text("INSERT INTO notes (body) VALUES ('note content')"))
            conn.commit()

        Schema.table("notes", lambda t: t.rename_column("body", "content"))

        with engine.connect() as conn:
            row = conn.execute(text("SELECT content FROM notes")).fetchone()
        assert row[0] == "note content"

    def test_rename_column_class_method(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("tags", lambda t: (t.id(), t.string("tag_name")))
        Schema.rename_column("tags", "tag_name", "name")

        with engine.connect() as conn:
            cols = _columns_of(conn, "tags")
        assert "name" in cols
        assert "tag_name" not in cols

    def test_multiple_renames_in_one_call(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("contacts", lambda t: (t.id(), t.string("fname"), t.string("lname")))

        def alter(t):
            t.rename_column("fname", "first_name")
            t.rename_column("lname", "last_name")

        Schema.table("contacts", alter)

        with engine.connect() as conn:
            cols = _columns_of(conn, "contacts")
        assert "first_name" in cols
        assert "last_name" in cols
        assert "fname" not in cols
        assert "lname" not in cols


# ===========================================================================
# rename_table
# ===========================================================================

class TestRenameTable:
    def test_rename_table_via_blueprint(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("old_name", lambda t: (t.id(), t.string("val")))

        Schema.table("old_name", lambda t: t.rename_table("new_name"))

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='new_name'")
            )
            assert result.fetchone() is not None

            result2 = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='old_name'")
            )
            assert result2.fetchone() is None

    def test_rename_table_via_schema_rename(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("tbl_a", lambda t: t.id())
        Schema.rename("tbl_a", "tbl_b")

        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='tbl_b'")
            )
            assert result.fetchone() is not None

    def test_rename_table_preserves_data(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("src", lambda t: (t.id(), t.string("msg")))

        with engine.connect() as conn:
            conn.execute(text("INSERT INTO src (msg) VALUES ('hello')"))
            conn.commit()

        Schema.table("src", lambda t: t.rename_table("dst"))

        with engine.connect() as conn:
            row = conn.execute(text("SELECT msg FROM dst")).fetchone()
        assert row[0] == "hello"


# ===========================================================================
# after() modifier (no-op)
# ===========================================================================

class TestAfterModifier:
    def test_after_is_a_noop_fluent(self):
        bp = Blueprint("posts")
        col = bp.string("title").after("id")
        assert col.after_column == "id"
        assert col.is_change is False

    def test_after_does_not_affect_sql(self):
        bp = Blueprint("posts")
        bp.string("title").after("id")
        sql = Blueprint._column_sql(bp.columns[0])
        assert "AFTER" not in sql


# ===========================================================================
# Combined operations in one Schema.table() call
# ===========================================================================

class TestCombinedAlterOperations:
    def test_add_and_rename_in_one_call(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("things", lambda t: (t.id(), t.string("old_field")))

        def alter(t):
            t.integer("new_count")           # ADD
            t.rename_column("old_field", "field")  # RENAME

        Schema.table("things", alter)

        with engine.connect() as conn:
            cols = _columns_of(conn, "things")
        assert "new_count" in cols
        assert "field" in cols
        assert "old_field" not in cols

    def test_change_and_add_in_one_call(self, monkeypatch):
        engine = _in_memory_engine()
        _patch_connection(engine, monkeypatch)

        Schema.create("events", lambda t: (t.id(), t.string("name", 50)))

        with engine.connect() as conn:
            conn.execute(text("INSERT INTO events (name) VALUES ('test')"))
            conn.commit()

        def alter(t):
            t.string("name", 255).change()           # CHANGE
            t.boolean("is_active").default(0)        # ADD (must have default for existing rows)

        Schema.table("events", alter)

        with engine.connect() as conn:
            cols = _columns_of(conn, "events")
            row = conn.execute(text("SELECT name FROM events")).fetchone()
        assert "is_active" in cols
        assert row[0] == "test"
