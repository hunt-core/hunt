"""Regression tests for the database audit findings: query builder.

DB-01, DB-02, DB-09, DB-10, DB-16, DB-L1, DB-L2, DB-L3, DB-L4, DB-L11,
HTTP-03 (first_or_fail) and the where_like / or_where_like API.
"""

from __future__ import annotations

import importlib

import pytest

from hunt.database.model import Model
from hunt.database.query_builder import QueryBuilder, _escape_like, relation
from hunt.database.schema.builder import Schema
from hunt.database.soft_deletes import SoftDeletes
from hunt.http.response import HttpException


@pytest.fixture
def db(monkeypatch):
    """A private in-memory SQLite database as the default connection."""
    conn_mod = importlib.import_module("hunt.database.connection")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECTION", "sqlite")
    monkeypatch.setenv("DB_DATABASE", ":memory:")
    monkeypatch.setattr(conn_mod, "_config", {})
    monkeypatch.setattr(conn_mod, "_connections", {})
    engine = conn_mod.connection()
    Schema.create(
        "qb_users",
        lambda bp: (bp.id(), bp.string("name"), bp.string("email").nullable(), bp.integer("tenant_id").nullable()),
    )
    Schema.create(
        "qb_posts",
        lambda bp: (
            bp.id(),
            bp.integer("user_id").nullable(),
            bp.string("title"),
            bp.string("status").default("draft"),
            bp.integer("views").default(0),
            bp.soft_deletes(),
        ),
    )
    yield engine
    engine.dispose()


class QUser(Model):
    table = "qb_users"
    timestamps = False
    fillable = ["name", "email", "tenant_id"]

    def posts(self):
        return self.has_many(QPost, "user_id")

    def purge(self):
        return self.delete()

    def not_a_relation(self):
        return 42


class QPost(SoftDeletes, Model):
    table = "qb_posts"
    timestamps = False
    fillable = ["user_id", "title", "status", "views"]

    def user(self):
        return self.belongs_to(QUser, "user_id")


def _titles(rows) -> list[str]:
    return sorted(r.title for r in rows)


# ---------------------------------------------------------------------------
# DB-01: insert_many
# ---------------------------------------------------------------------------


class TestInsertMany:
    def test_malicious_key_in_later_row_raises_and_inserts_nothing(self, db):
        rows = [
            {"name": "a", "email": "a@example.com"},
            {"name": "b", "email) VALUES ('x', (SELECT name FROM qb_users)); --": "b@example.com"},
        ]
        with pytest.raises(ValueError):
            QueryBuilder("qb_users").insert_many(rows)
        assert QueryBuilder("qb_users").count() == 0

    def test_malicious_key_through_create_many_on_unguarded_model(self, db):
        class Open(Model):
            table = "qb_users"
            timestamps = False
            guarded: list = []

        with pytest.raises(ValueError):
            Open.create_many([{"name": "a"}, {"name) VALUES ('x'); --": "b"}])
        assert QueryBuilder("qb_users").count() == 0

    def test_rows_with_different_key_order_land_in_the_right_columns(self, db):
        QueryBuilder("qb_users").insert_many(
            [{"name": "n1", "email": "e1"}, {"email": "e2", "name": "n2"}],
        )
        rows = {r["name"]: r["email"] for r in QueryBuilder("qb_users").get()}
        assert rows == {"n1": "e1", "n2": "e2"}

    def test_ragged_rows_raise(self, db):
        with pytest.raises(ValueError):
            QueryBuilder("qb_users").insert_many([{"name": "a", "email": "e"}, {"name": "b"}])
        with pytest.raises(ValueError):
            QueryBuilder("qb_users").insert_many([{"name": "a"}, {"name": "b", "email": "e"}])
        assert QueryBuilder("qb_users").count() == 0

    def test_first_row_keys_are_validated(self, db):
        with pytest.raises(ValueError):
            QueryBuilder("qb_users").insert_many([{"name; DROP TABLE qb_users": "a"}])

    def test_dotted_column_names_use_positional_binds(self, db, monkeypatch):
        # _ident accepts table.column; a bind named ":_set_a.b" would be broken.
        captured: list[tuple[str, dict]] = []

        class _Result:
            rowcount = 1
            lastrowid = 1

        def fake_write(self, sql, bindings):
            captured.append((sql, dict(bindings)))
            return _Result()

        monkeypatch.setattr(QueryBuilder, "_write", fake_write)
        QueryBuilder("qb_users").where("qb_users.name", "a").update({"qb_users.name": "b"})
        QueryBuilder("qb_users").where("id", 1).increment("qb_users.tenant_id", 2, {"qb_users.name": "c"})
        assert captured[0] == (
            "UPDATE qb_users SET qb_users.name = :_set_0 WHERE qb_users.name = :w0",
            {"w0": "a", "_set_0": "b"},
        )
        assert captured[1] == (
            "UPDATE qb_users SET qb_users.tenant_id = qb_users.tenant_id + :_adj_amount, qb_users.name = :_adj_0"
            " WHERE id = :w0",
            {"w0": 1, "_adj_amount": 2, "_adj_0": "c"},
        )

    def test_insert_uses_positional_binds(self, db):
        from hunt.database import debug

        debug.reset_query_tracker()
        QueryBuilder("qb_users").insert({"name": "a", "email": "e"})
        assert debug.get_query_log()[-1][0] == "INSERT INTO qb_users (name, email) VALUES (:_v0, :_v1)"
        QueryBuilder("qb_users").insert_many([{"name": "b", "email": "f"}])
        assert debug.get_query_log()[-1][0] == "INSERT INTO qb_users (name, email) VALUES (:_r0_0, :_r0_1)"
        with pytest.raises(ValueError):
            QueryBuilder("qb_users").insert({"name) VALUES ('x'); --": "a"})

    def test_identifier_with_trailing_newline_is_rejected(self):
        # DB-L3: "$" with match() accepted "id\n".
        from hunt.database.identifiers import qualified_ident, simple_ident

        for bad in ("id\n", "id ", "a.b.c", "1a", "a-b", ""):
            with pytest.raises(ValueError):
                qualified_ident(bad)
        for bad in ("id\n", "a.b", "a b"):
            with pytest.raises(ValueError):
                simple_ident(bad)
        assert qualified_ident("posts.title") == "posts.title"
        assert qualified_ident("*") == "*"
        with pytest.raises(ValueError):
            QueryBuilder("users\n")


# ---------------------------------------------------------------------------
# DB-02: or_where cannot escape scopes
# ---------------------------------------------------------------------------


class TestOrWhereScoping:
    def _seed(self):
        u1 = QUser.create(name="u1", tenant_id=1)
        u2 = QUser.create(name="u2", tenant_id=2)
        QPost.create(title="a", user_id=u1.id, status="draft")
        QPost.create(title="b", user_id=u1.id, status="published")
        trashed = QPost.create(title="b", user_id=u2.id, status="published")
        trashed.delete()
        QPost.create(title="c", user_id=u2.id, status="published")
        return u1, u2

    def test_or_where_does_not_return_trashed_rows(self, db):
        self._seed()
        rows = QPost.where("title", "a").or_where("title", "b").get()
        assert _titles(rows) == ["a", "b"]
        assert all(r.deleted_at is None for r in rows)

    def test_sql_shape_scope_and_user_group(self, db):
        sql, bindings = QPost.where("title", "a").or_where("title", "b")._materialize()._build_select()
        assert sql == "SELECT * FROM qb_posts WHERE qb_posts.deleted_at IS NULL AND (title = :w0 OR title = :w1)"
        assert bindings == {"w0": "a", "w1": "b"}

    def test_clauses_render_in_call_order_with_normal_precedence(self, db):
        self._seed()
        # a OR (b AND published): the draft "a" row and the live published "b".
        qb = QPost.where("title", "a").or_where("title", "b").where("status", "published")
        clause, _ = qb._materialize()._build_where_clause()
        assert clause == "qb_posts.deleted_at IS NULL AND (title = :w0 OR title = :w1 AND status = :w2)"
        assert _titles(qb.get()) == ["a", "b"]
        # Previously this was (a AND published) OR b.
        qb2 = QPost.where("title", "a").where("status", "published").or_where("title", "c")
        assert _titles(qb2.get()) == ["c"]

    def test_pure_and_queries_are_not_parenthesised(self):
        clause, bindings = QueryBuilder("t").where("a", 1).where("b", ">", 2)._build_where_clause()
        assert clause == "a = :w0 AND b > :w1"
        assert bindings == {"w0": 1, "w1": 2}

    def test_or_only_query_without_scope(self):
        clause, _ = QueryBuilder("t").where("a", 1).or_where("b", 2)._build_where_clause()
        assert clause == "a = :w0 OR b = :w1"

    def test_global_scope_is_anded_with_or_where(self, db):
        self._seed()

        class TenantUser(Model):
            table = "qb_users"
            timestamps = False

            @classmethod
            def boot(cls):
                cls.add_global_scope("tenant", lambda q: q.where("tenant_id", 1))

        rows = TenantUser.where("name", "u1").or_where("name", "u2").get()
        assert [r.name for r in rows] == ["u1"]
        clause, _ = TenantUser.where("name", "u1").or_where("name", "u2")._materialize()._build_where_clause()
        assert clause == "(tenant_id = :w0) AND (name = :w1 OR name = :w2)"

    def test_global_scope_using_or_is_kept_as_one_group(self, db):
        class Scoped(Model):
            table = "qb_users"
            timestamps = False

            @classmethod
            def boot(cls):
                cls.add_global_scope("s", lambda q: q.where("tenant_id", 1).or_where("tenant_id", 2))

        clause, _ = Scoped.where("name", "x")._materialize()._build_where_clause()
        assert clause == "(tenant_id = :w0 OR tenant_id = :w1) AND name = :w2"

    def test_update_and_delete_with_or_where_respect_scopes(self, db):
        self._seed()

        class TenantUser(Model):
            table = "qb_users"
            timestamps = False

            @classmethod
            def boot(cls):
                cls.add_global_scope("tenant", lambda q: q.where("tenant_id", 1))

        assert TenantUser.where("name", "u1").or_where("name", "u2").update({"email": "x"}) == 1
        assert QUser.where("name", "u2").first().email is None
        assert TenantUser.where("name", "u1").or_where("name", "u2").delete() == 1
        assert [u.name for u in QUser.all().get()] == ["u2"]

        # Soft-delete scope on update: the trashed "b" row is not touched.
        assert QPost.where("title", "a").or_where("title", "b").update({"views": 9}) == 2
        trashed = QPost.query().only_trashed().first()
        assert trashed.views == 0

    def test_eager_load_constraint_with_or_where_stays_inside_parent_keys(self, db):
        u1, _u2 = self._seed()
        users = (
            QUser.where("id", u1.id)
            .with_({"posts": lambda q: q.where("title", "zzz").or_where("status", "published")})
            .get()
        )
        loaded = users[0]._relations["posts"]
        assert _titles(loaded) == ["b"]
        assert all(p.user_id == u1.id for p in loaded)

    def test_where_group_still_works(self, db):
        self._seed()
        qb = QPost.where("status", "published").where_group(lambda q: q.where("title", "a").or_where("title", "c"))
        clause, _ = qb._materialize()._build_where_clause()
        assert clause == "qb_posts.deleted_at IS NULL AND status = :w0 AND (title = :w1 OR title = :w2)"
        assert _titles(qb.get()) == ["c"]

    def test_or_where_group(self):
        qb = QueryBuilder("t").where("a", 1).or_where_group(lambda q: q.where("b", 2).where("c", 3))
        clause, bindings = qb._build_where_clause()
        assert clause == "a = :w0 OR (b = :w1 AND c = :w2)"
        assert bindings == {"w0": 1, "w1": 2, "w2": 3}

    def test_raw_where_bindings_do_not_collide_in_groups(self, db):
        self._seed()
        qb = QPost.where_raw("views >= ?", [0]).where_group(
            lambda q: q.where_raw("title = ?", ["a"]).or_where("title", "c")
        )
        sql, bindings = qb._materialize()._build_select()
        assert sorted(bindings.values(), key=str) == [0, "a", "c"]
        assert len(bindings) == 3
        assert _titles(qb.get()) == ["a", "c"]


# ---------------------------------------------------------------------------
# DB-09: None handling
# ---------------------------------------------------------------------------


class TestWhereNone:
    def test_three_arg_none_is_not_mistaken_for_two_arg(self):
        clause, bindings = QueryBuilder("t").where("role", "!=", None)._build_where_clause()
        assert clause == "role IS NOT NULL"
        assert bindings == {}

    def test_not_equal_alias(self):
        assert QueryBuilder("t").where("role", "<>", None)._build_where_clause()[0] == "role IS NOT NULL"

    def test_equals_none_is_is_null(self):
        assert QueryBuilder("t").where("role", "=", None)._build_where_clause()[0] == "role IS NULL"
        assert QueryBuilder("t").where("role", None)._build_where_clause()[0] == "role IS NULL"

    def test_other_operators_with_none_raise(self):
        for op in (">", "<", ">=", "<=", "LIKE", "IN", "BETWEEN"):
            with pytest.raises(ValueError):
                QueryBuilder("t").where("role", op, None)

    def test_or_where_and_having_follow_the_same_rules(self):
        clause, _ = QueryBuilder("t").where("a", 1).or_where("b", "!=", None)._build_where_clause()
        assert clause == "a = :w0 OR b IS NOT NULL"
        sql, _ = QueryBuilder("t").group_by("a").having("b", "!=", None)._build_select()
        assert sql.endswith("HAVING b IS NOT NULL")
        with pytest.raises(ValueError):
            QueryBuilder("t").or_where("b", ">", None)

    def test_two_arg_value_that_looks_like_an_operator_stays_a_value(self):
        clause, bindings = QueryBuilder("t").where("symbol", "!=")._build_where_clause()
        assert clause == "symbol = :w0"
        assert bindings == {"w0": "!="}

    def test_invalid_operator_rejected(self):
        with pytest.raises(ValueError):
            QueryBuilder("t").where("a", "; DROP TABLE t", 1)

    def test_model_classmethods_forward_the_sentinel(self, db):
        QUser.create(name="a", email=None)
        QUser.create(name="b", email="b@example.com")
        assert [u.name for u in QUser.where("email", "!=", None).get()] == ["b"]
        assert [u.name for u in QUser.where("email", None).get()] == ["a"]
        assert [u.name for u in QUser.or_where("email", "=", None).get()] == ["a"]


# ---------------------------------------------------------------------------
# DB-L1: shared clause renderer
# ---------------------------------------------------------------------------


class TestClauseRenderer:
    def test_empty_where_in_matches_nothing(self, db):
        QUser.create(name="a")
        clause, bindings = QueryBuilder("t").where_in("id", [])._build_where_clause()
        assert clause == "1 = 0"
        assert bindings == {}
        assert QUser.where_in("id", []).count() == 0
        assert QUser.where_not_in("id", []).count() == 1

    def test_three_arg_in_and_between(self, db):
        for name in ("a", "b", "c"):
            QUser.create(name=name)
        assert QUser.where("id", "IN", [1, 3]).count() == 2
        assert QUser.where("id", "not in", (1, 3)).count() == 1
        assert QUser.where("id", "BETWEEN", [2, 3]).count() == 2
        clause, bindings = QueryBuilder("t").where("id", "BETWEEN", [2, 3])._build_where_clause()
        assert clause == "id BETWEEN :w0_0 AND :w0_1"
        assert bindings == {"w0_0": 2, "w0_1": 3}

    def test_or_where_in(self, db):
        for name in ("a", "b", "c"):
            QUser.create(name=name)
        qb = QUser.where("name", "a").or_where("id", "IN", [2, 3])
        assert qb.count() == 3
        clause, _ = QueryBuilder("t").where("name", "a").or_where("id", "IN", [2, 3])._build_where_clause()
        assert clause == "name = :w0 OR id IN (:w1_0, :w1_1)"

    def test_in_requires_a_list(self):
        with pytest.raises(ValueError):
            QueryBuilder("t").where("id", "IN", "abc")
        with pytest.raises(ValueError):
            QueryBuilder("t").where("id", "BETWEEN", [1])

    def test_having_in(self, db):
        for uid, title in ((1, "a"), (1, "b"), (2, "c")):
            QPost.create(user_id=uid, title=title)
        rows = (
            QueryBuilder("qb_posts")
            .select_raw("user_id", "COUNT(*) AS n")
            .group_by("user_id")
            .having("user_id", "IN", [1])
            .get()
        )
        assert rows == [{"user_id": 1, "n": 2}]

    def test_empty_update_is_rejected(self, db):
        with pytest.raises(ValueError):
            QueryBuilder("qb_users").where("id", 1).update({})

    def test_ilike_is_emulated_outside_postgresql(self, db):
        QUser.create(name="Alice")
        assert QUser.where("name", "ILIKE", "alice").count() == 1
        assert QUser.where("name", "NOT ILIKE", "alice").count() == 0
        clause, _ = QUser.where("name", "ILIKE", "alice")._build_where_clause()
        assert clause == "LOWER(name) LIKE LOWER(:w0)"

    def test_ilike_is_native_on_postgresql(self, monkeypatch):
        qb = QueryBuilder("t").where("name", "ILIKE", "a%")
        monkeypatch.setattr(QueryBuilder, "_dialect_name", lambda self: "postgresql")
        assert qb._build_where_clause()[0] == "name ILIKE :w0"

    def test_negative_limit_and_offset_rejected(self):
        with pytest.raises(ValueError):
            QueryBuilder("t").limit(-1)
        with pytest.raises(ValueError):
            QueryBuilder("t").offset(-5)

    def test_offset_without_limit_is_valid_sql(self, db):
        for name in ("a", "b", "c"):
            QUser.create(name=name)
        assert [u.name for u in QUser.order_by("id").offset(1).get()] == ["b", "c"]


# ---------------------------------------------------------------------------
# where_like / or_where_like
# ---------------------------------------------------------------------------


class TestWhereLike:
    def test_escape_helper(self):
        assert _escape_like("50%_off!") == "50!%!_off!!"

    def test_sql_has_explicit_escape_clause(self):
        clause, bindings = (
            QueryBuilder("t").where_like("name", "50%").or_where_like("email", "a_b")._build_where_clause()
        )
        assert clause == "name LIKE :w0 ESCAPE '!' OR email LIKE :w1 ESCAPE '!'"
        assert bindings == {"w0": "%50!%%", "w1": "%a!_b%"}

    def test_postgresql_uses_ilike_and_casts(self, monkeypatch):
        monkeypatch.setattr(QueryBuilder, "_dialect_name", lambda self: "postgresql")
        clause, _ = QueryBuilder("t").where_like("id", "1")._build_where_clause()
        assert clause == "CAST(id AS TEXT) ILIKE :w0 ESCAPE '!'"

    def test_wildcards_in_the_term_are_literal(self, db):
        QUser.create(name="100% legit", email="a_b@example.com")
        QUser.create(name="100 percent", email="aXb@example.com")
        QUser.create(name="bang!", email="c@example.com")
        assert [u.name for u in QUser.where_like("name", "100%").get()] == ["100% legit"]
        assert [u.name for u in QUser.where_like("email", "a_b").get()] == ["100% legit"]
        assert [u.name for u in QUser.where_like("name", "!").get()] == ["bang!"]
        assert QUser.where_like("name", "%").count() == 1
        assert QUser.where_like("name", "").count() == 3

    def test_case_insensitive_contains(self, db):
        QUser.create(name="Alice Smith")
        assert QUser.where_like("name", "alice").count() == 1
        assert QUser.where_like("name", "SMITH").count() == 1

    def test_search_group_is_anded_with_other_filters_and_soft_deletes(self, db):
        QPost.create(title="hello world", status="draft")
        QPost.create(title="other", status="hello")
        gone = QPost.create(title="hello trashed", status="draft")
        gone.delete()
        qb = QPost.where("status", "draft").where_group(
            lambda q: q.where_like("title", "hello").or_where_like("status", "hello")
        )
        assert _titles(qb.get()) == ["hello world"]
        # Even without a group, the OR cannot leak trashed rows.
        assert _titles(QPost.where_like("title", "hello").or_where_like("status", "hello").get()) == [
            "hello world",
            "other",
        ]


# ---------------------------------------------------------------------------
# DB-10: count / paginate
# ---------------------------------------------------------------------------


class TestCountAndPaginate:
    def _seed(self):
        for uid, title in ((1, "a"), (1, "b"), (1, "c"), (2, "d"), (3, "e")):
            QPost.create(user_id=uid, title=title)

    def test_count_with_group_by_counts_groups(self, db):
        self._seed()
        assert QPost.query().group_by("user_id").select("user_id").count() == 3

    def test_count_with_distinct(self, db):
        self._seed()
        assert QPost.query().select("user_id").distinct().count() == 3
        assert QPost.query().select("user_id").count() == 5

    def test_count_with_having(self, db):
        self._seed()
        qb = QueryBuilder("qb_posts").select_raw("user_id", "COUNT(*) AS n").group_by("user_id").having("n", ">", 1)
        assert qb.count() == 1

    def test_paginate_total_with_group_by(self, db):
        self._seed()
        page = QueryBuilder("qb_posts").select("user_id").group_by("user_id").order_by("user_id").paginate(2, 1)
        assert page.total == 3
        assert page.last_page == 2
        assert [r["user_id"] for r in page.data] == [1, 2]

    def test_per_page_zero_and_negative_are_clamped(self, db):
        self._seed()
        page = QPost.order_by("id").paginate(per_page=0, page=1)
        assert page.per_page == 1 and len(page.data) == 1 and page.last_page == 5
        page = QPost.order_by("id").paginate(per_page=-1, page=1)
        assert page.per_page == 1 and len(page.data) == 1

    def test_page_zero_and_negative_are_clamped(self, db):
        self._seed()
        for bad in (0, -3):
            page = QPost.order_by("id").paginate(per_page=2, page=bad)
            assert page.current_page == 1
            assert [p.title for p in page.data] == ["a", "b"]

    def test_non_numeric_input_falls_back(self, db):
        self._seed()
        page = QPost.order_by("id").paginate(per_page="abc", page="xyz")
        assert (page.per_page, page.current_page) == (15, 1)

    def test_per_page_upper_bound(self, db, monkeypatch):
        self._seed()
        monkeypatch.setattr(QueryBuilder, "max_per_page", 3)
        page = QPost.order_by("id").paginate(per_page=10_000_000, page=1)
        assert page.per_page == 3 and len(page.data) == 3

    def test_simple_paginate_is_clamped_too(self, db):
        self._seed()
        page = QPost.order_by("id").simple_paginate(per_page=0, page=-1)
        assert page.per_page == 1 and page.current_page == 1 and len(page.data) == 1
        assert page.has_more_pages is True


# ---------------------------------------------------------------------------
# DB-16: eager-load names
# ---------------------------------------------------------------------------


class TestEagerLoadNames:
    def test_framework_method_name_is_rejected_and_nothing_is_deleted(self, db):
        QUser.create(name="a")
        with pytest.raises(ValueError):
            QUser.where("name", "a").with_("delete").get()
        with pytest.raises(ValueError):
            QUser.with_("force_delete")
        with pytest.raises(ValueError):
            QUser.with_("save")
        assert QUser.query().count() == 1

    def test_unknown_private_and_malformed_names_are_rejected(self, db):
        for bad in ("nope", "_instance_query", "__class__", "posts; DROP", "", "query", "fillable"):
            with pytest.raises(ValueError):
                QUser.with_(bad)

    def test_user_method_that_touches_the_database_is_rejected_without_side_effects(self, db):
        QUser.create(name="a")
        with pytest.raises(ValueError):
            QUser.with_("purge").get()
        assert QUser.query().count() == 1

    def test_method_that_does_not_return_a_relation_is_rejected(self, db):
        QUser.create(name="a")
        with pytest.raises(ValueError):
            QUser.with_("not_a_relation").get()

    def test_method_with_required_arguments_is_rejected(self, db):
        class WithArgs(Model):
            table = "qb_users"

            def related(self, kind):
                return self.has_many(QPost, "user_id")

        with pytest.raises(ValueError):
            WithArgs.with_("related")

    def test_valid_relations_still_load(self, db):
        u = QUser.create(name="a")
        QPost.create(title="t", user_id=u.id)
        users = QUser.with_("posts").get()
        assert [p.title for p in users[0]._relations["posts"]] == ["t"]
        posts = QPost.with_("user").get()
        assert posts[0]._relations["user"].name == "a"

    def test_relation_decorator_makes_the_model_strict(self, db):
        class Strict(Model):
            table = "qb_users"
            timestamps = False

            @relation
            def posts(self):
                return self.has_many(QPost, "user_id")

            def unmarked(self):
                return self.has_many(QPost, "user_id")

        Strict.query().insert({"name": "a"})
        assert Strict.with_("posts").get()[0]._relations["posts"] == []
        with pytest.raises(ValueError):
            Strict.with_("unmarked")


# ---------------------------------------------------------------------------
# DB-L2 / DB-L4 / DB-L11 / HTTP-03
# ---------------------------------------------------------------------------


class TestMisc:
    def test_cache_key_includes_connection_name(self):
        a = QueryBuilder("t", conn_name="tenant_a").where("id", 1)._derive_cache_key()
        b = QueryBuilder("t", conn_name="tenant_b").where("id", 1)._derive_cache_key()
        assert a != b
        assert a == QueryBuilder("t", conn_name="tenant_a").where("id", 1)._derive_cache_key()

    def test_collection_protocol_runs_one_query(self, db):
        from hunt.database import debug

        for name in ("a", "b"):
            QUser.create(name=name)
        debug.reset_query_tracker()
        qb = QUser.where("name", "!=", "zzz")
        assert bool(qb) is True
        assert len(qb) == 2
        assert [u.name for u in qb] == ["a", "b"]
        assert list(qb) and len(debug.get_query_log()) == 1

    def test_empty_builder_is_falsy(self, db):
        assert not QUser.where("name", "nobody")

    def test_pluck_with_table_prefix(self, db):
        QUser.create(name="a")
        assert QUser.query().pluck("qb_users.name") == ["a"]

    def test_chunk_and_each_order_by_primary_key(self, db):
        for name in ("a", "b", "c"):
            QUser.create(name=name)
        seen: list[list[int]] = []
        QUser.query().chunk(2, lambda rows: seen.append([r.id for r in rows]))
        assert seen == [[1, 2], [3]]
        assert QUser.query()._stable_order()._order_bys == [("qb_users.id", "ASC")]
        # An explicit order is left alone.
        assert QUser.order_by("name", "desc")._stable_order()._order_bys == [("name", "DESC")]
        names: list[str] = []
        QUser.query().each(lambda row, i: names.append(row.name))
        assert names == ["a", "b", "c"]

    def test_chunk_rejects_non_positive_size(self, db):
        with pytest.raises(ValueError):
            QUser.query().chunk(0, lambda rows: None)

    def test_first_or_fail_raises_404(self, db):
        with pytest.raises(HttpException) as exc:
            QUser.where("id", 999).first_or_fail()
        assert exc.value.status == 404

    def test_find_or_fail_raises_404(self, db):
        with pytest.raises(HttpException) as exc:
            QUser.find_or_fail(999)
        assert exc.value.status == 404
        with pytest.raises(HttpException):
            QUser.query().find_or_fail(999)
        u = QUser.create(name="a")
        assert QUser.find_or_fail(u.id).name == "a"


# ---------------------------------------------------------------------------
# remember(): TTL semantics shared with the cache layer
# ---------------------------------------------------------------------------


class _FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.puts: list[tuple[str, object]] = []

    def get(self, key, default=None):
        import json

        # Round-trip like a real store: tuples come back as lists.
        return json.loads(self.store[key]) if key in self.store else default

    def put(self, key, value, seconds=None):
        import json

        self.puts.append((key, seconds))
        if seconds is not None and seconds <= 0:
            self.store.pop(key, None)
            return
        self.store[key] = json.dumps(value)


class TestRemember:
    @pytest.fixture
    def cache(self, monkeypatch):
        import hunt.cache.manager as cache_manager

        fake = _FakeCache()
        monkeypatch.setattr(cache_manager, "Cache", fake)
        return fake

    def test_positive_ttl_caches_and_rehydrates_models(self, db, cache):
        QUser.create(name="a")
        first = QUser.where("name", "a").remember(60).get()
        QUser.where("name", "a").update({"name": "changed"})
        second = QUser.where("name", "a").remember(60).get()
        assert [u.name for u in first] == [u.name for u in second] == ["a"]
        assert isinstance(second[0], QUser) and second[0]._exists is True
        assert [ttl for _key, ttl in cache.puts] == [60]

    def test_none_ttl_means_forever(self, db, cache):
        QUser.create(name="a")
        QUser.query().remember(None).get()
        assert [ttl for _key, ttl in cache.puts] == [None]
        QUser.create(name="b")
        assert len(QUser.query().remember(None).get()) == 1

    def test_zero_or_negative_ttl_does_not_cache(self, db, cache):
        QUser.create(name="a")
        for ttl in (0, -5):
            assert len(QUser.query().remember(ttl).get()) == 1
        assert cache.puts == [] and cache.store == {}

    def test_plain_query_does_not_touch_the_cache(self, db, cache):
        QUser.query().get()
        assert cache.puts == []


class TestGetRelation:
    def test_returns_the_relation_object(self, db):
        from hunt.database.relations.has_many import HasMany

        user = QUser.create(name="a")
        assert isinstance(user.get_relation("posts"), HasMany)

    def test_rejects_non_relations_without_running_them(self, db):
        user = QUser.create(name="a")
        for bad in ("delete", "purge", "not_a_relation", "nope", "_instance_query"):
            with pytest.raises(ValueError):
                user.get_relation(bad)
        assert QUser.query().count() == 1
