"""Tests for Phase A feature implementations."""
from __future__ import annotations

import pytest
from datetime import datetime


# ===========================================================================
# A1 — Database Transactions
# ===========================================================================

class TestDbTransactions:
    def test_transaction_exported(self):
        from hunt.database import transaction, begin
        assert callable(transaction)
        assert callable(begin)

    def test_transaction_commits(self):
        from sqlalchemy import text
        from hunt.database.connection import connection, _connections
        import os
        os.environ["DB_CONNECTION"] = "sqlite"
        os.environ["DB_DATABASE"] = ":memory:"
        _connections.clear()

        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE tx_test (id INTEGER PRIMARY KEY, val TEXT)"))
            c.commit()

        from hunt.database import transaction
        transaction(lambda conn: conn.execute(text("INSERT INTO tx_test (val) VALUES ('hello')")))

        with eng.connect() as c:
            rows = c.execute(text("SELECT val FROM tx_test")).fetchall()
        assert rows[0][0] == "hello"

    def test_begin_context_manager_commits(self):
        from sqlalchemy import text
        from hunt.database.connection import connection, _connections
        import os
        os.environ["DB_CONNECTION"] = "sqlite"
        os.environ["DB_DATABASE"] = ":memory:"
        _connections.clear()

        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE begin_test (id INTEGER PRIMARY KEY, val TEXT)"))
            c.commit()

        from hunt.database import begin
        with begin() as conn:
            conn.execute(text("INSERT INTO begin_test (val) VALUES ('world')"))

        with eng.connect() as c:
            rows = c.execute(text("SELECT val FROM begin_test")).fetchall()
        assert rows[0][0] == "world"

    def test_begin_context_manager_rolls_back_on_exception(self):
        from sqlalchemy import text
        from hunt.database.connection import connection, _connections
        import os
        os.environ["DB_CONNECTION"] = "sqlite"
        os.environ["DB_DATABASE"] = ":memory:"
        _connections.clear()

        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE rollback_test (id INTEGER PRIMARY KEY, val TEXT)"))
            c.commit()

        from hunt.database import begin
        with pytest.raises(RuntimeError):
            with begin() as conn:
                conn.execute(text("INSERT INTO rollback_test (val) VALUES ('oops')"))
                raise RuntimeError("simulated failure")

        with eng.connect() as c:
            rows = c.execute(text("SELECT val FROM rollback_test")).fetchall()
        assert rows == []


# ===========================================================================
# A2 — Eager Loading
# ===========================================================================

class TestEagerLoading:
    @pytest.fixture(autouse=True)
    def setup_db(self):
        import os
        from hunt.database.connection import _connections
        os.environ["DB_CONNECTION"] = "sqlite"
        os.environ["DB_DATABASE"] = ":memory:"
        _connections.clear()
        from sqlalchemy import text
        from hunt.database.connection import connection
        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE posts (id INTEGER PRIMARY KEY, title TEXT)"))
            c.execute(text("CREATE TABLE comments (id INTEGER PRIMARY KEY, post_id INTEGER, body TEXT)"))
            c.execute(text("INSERT INTO posts (id, title) VALUES (1, 'Post One')"))
            c.execute(text("INSERT INTO posts (id, title) VALUES (2, 'Post Two')"))
            c.execute(text("INSERT INTO comments (id, post_id, body) VALUES (1, 1, 'Comment A')"))
            c.execute(text("INSERT INTO comments (id, post_id, body) VALUES (2, 1, 'Comment B')"))
            c.execute(text("INSERT INTO comments (id, post_id, body) VALUES (3, 2, 'Comment C')"))
            c.commit()

    def test_with_has_many_loads_relations(self):
        from hunt.database.model import Model

        class Comment(Model):
            table = "comments"
            timestamps = False

        class Post(Model):
            table = "posts"
            timestamps = False

            def comments(self):
                return self.has_many(Comment, "post_id")

        posts = Post.with_("comments").get()
        assert len(posts) == 2
        post1 = next(p for p in posts if p._attributes["id"] == 1)
        post2 = next(p for p in posts if p._attributes["id"] == 2)
        assert len(post1._relations["comments"]) == 2
        assert len(post2._relations["comments"]) == 1

    def test_with_single_query_per_relation(self):
        """Eager loading should load relations into _relations for all models."""
        from hunt.database.model import Model

        class Comment(Model):
            table = "comments"
            timestamps = False

        class Post(Model):
            table = "posts"
            timestamps = False

            def comments(self):
                return self.has_many(Comment, "post_id")

        posts = Post.with_("comments").get()
        assert all("comments" in p._relations for p in posts)

    def test_with_belongs_to_loads_parent(self):
        from hunt.database.model import Model

        class Post(Model):
            table = "posts"
            timestamps = False

        class Comment(Model):
            table = "comments"
            timestamps = False

            def post(self):
                return self.belongs_to(Post, "post_id")

        comments = Comment.with_("post").get()
        assert all("post" in c._relations for c in comments)
        comment_with_post1 = next(c for c in comments if c._attributes["post_id"] == 1)
        assert comment_with_post1._relations["post"]._attributes["title"] == "Post One"

    def test_with_constrained_relation(self):
        from hunt.database.model import Model

        class Comment(Model):
            table = "comments"
            timestamps = False

        class Post(Model):
            table = "posts"
            timestamps = False

            def comments(self):
                return self.has_many(Comment, "post_id")

        posts = Post.with_({"comments": lambda q: q.where("body", "Comment A")}).get()
        post1 = next(p for p in posts if p._attributes["id"] == 1)
        assert len(post1._relations["comments"]) == 1
        assert post1._relations["comments"][0]._attributes["body"] == "Comment A"

    def test_model_with_classmethod(self):
        from hunt.database.model import Model

        class Comment(Model):
            table = "comments"
            timestamps = False

        class Post(Model):
            table = "posts"
            timestamps = False

            def comments(self):
                return self.has_many(Comment, "post_id")

        posts = Post.with_("comments").get()
        assert isinstance(posts, list)

    def test_replicate(self):
        from hunt.database.model import Model

        class Post(Model):
            table = "posts"
            timestamps = False
            fillable = ["title"]

        post = Post.__new__(Post)
        post._attributes = {"id": 1, "title": "Hello"}
        post._original = {"id": 1, "title": "Hello"}
        post._exists = True
        post._relations = {}

        copy = post.replicate()
        assert copy._exists is False
        assert "id" not in copy._attributes
        assert copy._attributes["title"] == "Hello"

    def test_appends(self):
        from hunt.database.model import Model

        class Post(Model):
            table = "posts"
            timestamps = False
            appends = ["title_upper"]

            def get_title_upper_attribute(self):
                return (self._attributes.get("title") or "").upper()

        post = Post.__new__(Post)
        post._attributes = {"id": 1, "title": "hello"}
        post._original = {}
        post._exists = True
        post._relations = {}

        d = post.to_dict()
        assert d["title_upper"] == "HELLO"


# ===========================================================================
# A3 — Request enhancements
# ===========================================================================

class TestRequestCookie:
    def _make_request(self, headers=None):
        from hunt.http.request import Request
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": headers or [],
        }
        return Request(scope)

    def test_cookie_reads_value(self):
        req = self._make_request([
            (b"cookie", b"foo=bar; baz=qux"),
        ])
        assert req.cookie("foo") == "bar"
        assert req.cookie("baz") == "qux"

    def test_cookie_returns_default_when_missing(self):
        req = self._make_request()
        assert req.cookie("missing", "default") == "default"

    def test_missing_is_inverse_of_has(self):
        from hunt.http.request import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        }
        req = Request(scope, b"name=Alice")
        assert req.has("name") is True
        assert req.missing("name") is False
        assert req.has("age") is False
        assert req.missing("age") is True


class TestUploadedFile:
    def test_file_parsed_from_multipart(self):
        from hunt.http.request import Request

        boundary = "----WebKitFormBoundary"
        body = (
            f"------WebKitFormBoundary\r\n"
            f'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            f"Content-Type: text/plain\r\n\r\n"
            f"hello world\r\n"
            f"------WebKitFormBoundary--\r\n"
        ).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [
                (b"content-type", f"multipart/form-data; boundary=----WebKitFormBoundary".encode()),
            ],
        }
        req = Request(scope, body)
        assert req.has_file("file")
        f = req.file("file")
        assert f.filename == "test.txt"
        assert f.content_type == "text/plain"
        assert f.content == b"hello world"
        assert f.size == 11

    def test_form_fields_alongside_files(self):
        from hunt.http.request import Request

        boundary = "myboundary"
        body = (
            f"--myboundary\r\n"
            f'Content-Disposition: form-data; name="name"\r\n\r\n'
            f"Alice\r\n"
            f"--myboundary\r\n"
            f'Content-Disposition: form-data; name="avatar"; filename="pic.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
            f"\x89PNG\r\n"
            f"--myboundary--\r\n"
        ).encode()

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "query_string": b"",
            "headers": [(b"content-type", b"multipart/form-data; boundary=myboundary")],
        }
        req = Request(scope, body)
        assert req.input("name") == "Alice"
        assert req.has_file("avatar")
        assert req.file("avatar").filename == "pic.png"

    def test_has_file_returns_false_when_no_file(self):
        from hunt.http.request import Request
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [],
        }
        req = Request(scope)
        assert req.has_file("anything") is False


# ===========================================================================
# A4 — Redirect enhancements
# ===========================================================================

class TestFluentRedirect:
    def test_redirect_returns_fluent_redirect(self):
        from hunt.http.response import redirect, FluentRedirect
        r = redirect("/foo")
        assert isinstance(r, FluentRedirect)
        assert r._headers["Location"] == "/foo"

    def test_to_changes_location(self):
        from hunt.http.response import redirect
        r = redirect("/old").to("/new")
        assert r._headers["Location"] == "/new"

    def test_back_falls_back_to_default_without_session(self):
        from hunt.http.response import FluentRedirect
        from unittest.mock import patch
        with patch("hunt.auth.manager._get_current_request", return_value=None):
            r = FluentRedirect().back(default="/home")
        assert r._headers["Location"] == "/home"

    def test_with_flashes_to_session(self):
        from hunt.http.response import FluentRedirect
        from unittest.mock import MagicMock, patch

        mock_req = MagicMock()
        mock_store = MagicMock()
        mock_req._session = mock_store

        with patch("hunt.auth.manager._get_current_request", return_value=mock_req):
            r = FluentRedirect("/done").with_("status", "Saved!")

        mock_store.flash.assert_called_once_with("status", "Saved!")

    def test_with_errors_flashes_validation_exception(self):
        from hunt.http.response import FluentRedirect
        from hunt.validation.validator import ValidationException
        from unittest.mock import MagicMock, patch

        mock_req = MagicMock()
        mock_store = MagicMock()
        mock_req._session = mock_store
        exc = ValidationException({"email": ["required"]})

        with patch("hunt.auth.manager._get_current_request", return_value=mock_req):
            FluentRedirect("/back").with_errors(exc)

        mock_store.flash.assert_called_once_with("_errors", {"email": ["required"]})

    def test_helpers_redirect_returns_fluent(self):
        from hunt.support.helpers import redirect
        from hunt.http.response import FluentRedirect
        assert isinstance(redirect("/x"), FluentRedirect)


# ===========================================================================
# A5 — Query Builder gaps
# ===========================================================================

class TestQueryBuilderGaps:
    @pytest.fixture(autouse=True)
    def setup_db(self):
        import os
        from hunt.database.connection import _connections
        os.environ["DB_CONNECTION"] = "sqlite"
        os.environ["DB_DATABASE"] = ":memory:"
        _connections.clear()
        from sqlalchemy import text
        from hunt.database.connection import connection
        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, score INTEGER DEFAULT 0)"))
            c.execute(text("INSERT INTO items (name, score) VALUES ('alpha', 10)"))
            c.execute(text("INSERT INTO items (name, score) VALUES ('beta', 20)"))
            c.execute(text("INSERT INTO items (name, score) VALUES ('gamma', 30)"))
            c.commit()

    def _qb(self):
        from hunt.database.query_builder import QueryBuilder
        return QueryBuilder("items")

    def test_when_applies_callback_when_true(self):
        qb = self._qb().when(True, lambda q: q.where("score", ">", 15))
        results = qb.get()
        assert len(results) == 2

    def test_when_skips_callback_when_false(self):
        qb = self._qb().when(False, lambda q: q.where("score", ">", 15))
        results = qb.get()
        assert len(results) == 3

    def test_group_by_and_having(self):
        from sqlalchemy import text
        from hunt.database.connection import connection
        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE sales (id INTEGER PRIMARY KEY, dept TEXT, amount INTEGER)"))
            c.execute(text("INSERT INTO sales (dept, amount) VALUES ('A', 100)"))
            c.execute(text("INSERT INTO sales (dept, amount) VALUES ('A', 200)"))
            c.execute(text("INSERT INTO sales (dept, amount) VALUES ('B', 50)"))
            c.commit()
        from hunt.database.query_builder import QueryBuilder
        results = (
            QueryBuilder("sales")
            .select_raw("dept", "SUM(amount) as total")
            .group_by("dept")
            .having("total", ">", 100)
            .get()
        )
        assert len(results) == 1
        assert results[0]["dept"] == "A"

    def test_distinct(self):
        from sqlalchemy import text
        from hunt.database.connection import connection
        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE dupes (id INTEGER PRIMARY KEY, color TEXT)"))
            c.execute(text("INSERT INTO dupes (color) VALUES ('red')"))
            c.execute(text("INSERT INTO dupes (color) VALUES ('red')"))
            c.execute(text("INSERT INTO dupes (color) VALUES ('blue')"))
            c.commit()
        from hunt.database.query_builder import QueryBuilder
        results = QueryBuilder("dupes").select("color").distinct().get()
        colors = [r["color"] for r in results]
        assert sorted(colors) == ["blue", "red"]

    def test_increment(self):
        self._qb().where("name", "alpha").increment("score", 5)
        rows = self._qb().where("name", "alpha").get()
        assert rows[0]["score"] == 15

    def test_decrement(self):
        self._qb().where("name", "beta").decrement("score", 5)
        rows = self._qb().where("name", "beta").get()
        assert rows[0]["score"] == 15

    def test_chunk_iterates_all(self):
        collected = []
        self._qb().chunk(2, lambda batch: collected.extend(batch))
        assert len(collected) == 3

    def test_chunk_stops_early_on_false(self):
        collected = []
        def cb(batch):
            collected.extend(batch)
            return False
        self._qb().chunk(2, cb)
        assert len(collected) == 2  # only first chunk

    def test_each_iterates_with_index(self):
        items = []
        self._qb().each(lambda item, i: items.append((i, item["name"])))
        assert items[0] == (0, "alpha")
        assert len(items) == 3

    def test_left_join(self):
        from sqlalchemy import text
        from hunt.database.connection import connection
        eng = connection()
        with eng.connect() as c:
            c.execute(text("CREATE TABLE tags (id INTEGER PRIMARY KEY, item_id INTEGER, label TEXT)"))
            c.execute(text("INSERT INTO tags (item_id, label) VALUES (1, 'featured')"))
            c.commit()
        from hunt.database.query_builder import QueryBuilder
        results = (
            QueryBuilder("items")
            .select_raw("items.name", "tags.label")
            .left_join("tags", "items.id", "tags.item_id")
            .get()
        )
        assert len(results) == 3  # all items, even without a tag


# ===========================================================================
# A6 — Validation gaps
# ===========================================================================

class TestValidationGaps:
    def _v(self, data, rules):
        from hunt.validation.validator import Validator
        return Validator.make(data, rules)

    def test_nullable_skips_other_rules_when_empty(self):
        v = self._v({"age": ""}, {"age": "nullable|integer"})
        assert v.passes()

    def test_nullable_still_validates_when_present(self):
        v = self._v({"age": "abc"}, {"age": "nullable|integer"})
        assert v.fails()

    def test_sometimes_skips_missing_field(self):
        v = self._v({}, {"age": "sometimes|required|integer"})
        assert v.passes()

    def test_sometimes_validates_when_present(self):
        v = self._v({"age": "abc"}, {"age": "sometimes|integer"})
        assert v.fails()

    def test_bail_stops_after_first_failure(self):
        v = self._v({"age": "abc"}, {"age": "bail|integer|min:0"})
        v._run()
        # only one error (integer), not two (integer + min)
        assert len(v._errors.get("age", [])) == 1

    def test_different_passes_when_values_differ(self):
        v = self._v({"a": "foo", "b": "bar"}, {"a": "different:b"})
        assert v.passes()

    def test_different_fails_when_same(self):
        v = self._v({"a": "foo", "b": "foo"}, {"a": "different:b"})
        assert v.fails()

    def test_required_if_triggers_when_condition_met(self):
        v = self._v({"role": "admin"}, {"secret": "required_if:role,admin"})
        assert v.fails()

    def test_required_if_passes_when_condition_not_met(self):
        v = self._v({"role": "user"}, {"secret": "required_if:role,admin"})
        assert v.passes()

    def test_required_unless_triggers(self):
        v = self._v({"role": "admin"}, {"secret": "required_unless:role,user"})
        assert v.fails()

    def test_gt(self):
        assert self._v({"n": "5"}, {"n": "gt:3"}).passes()
        assert self._v({"n": "3"}, {"n": "gt:3"}).fails()

    def test_gte(self):
        assert self._v({"n": "3"}, {"n": "gte:3"}).passes()
        assert self._v({"n": "2"}, {"n": "gte:3"}).fails()

    def test_lt(self):
        assert self._v({"n": "2"}, {"n": "lt:3"}).passes()
        assert self._v({"n": "3"}, {"n": "lt:3"}).fails()

    def test_lte(self):
        assert self._v({"n": "3"}, {"n": "lte:3"}).passes()
        assert self._v({"n": "4"}, {"n": "lte:3"}).fails()

    def test_ip_valid(self):
        assert self._v({"ip": "192.168.1.1"}, {"ip": "ip"}).passes()
        assert self._v({"ip": "::1"}, {"ip": "ip"}).passes()
        assert self._v({"ip": "not-an-ip"}, {"ip": "ip"}).fails()

    def test_uuid_valid(self):
        import uuid
        assert self._v({"id": str(uuid.uuid4())}, {"id": "uuid"}).passes()
        assert self._v({"id": "not-a-uuid"}, {"id": "uuid"}).fails()

    def test_json_valid(self):
        assert self._v({"data": '{"key": "val"}'}, {"data": "json"}).passes()
        assert self._v({"data": "not json"}, {"data": "json"}).fails()

    def test_starts_with(self):
        assert self._v({"s": "hello world"}, {"s": "starts_with:hello"}).passes()
        assert self._v({"s": "world"}, {"s": "starts_with:hello"}).fails()

    def test_ends_with(self):
        assert self._v({"s": "hello world"}, {"s": "ends_with:world"}).passes()
        assert self._v({"s": "hello"}, {"s": "ends_with:world"}).fails()

    def test_date_valid(self):
        assert self._v({"d": "2024-01-15"}, {"d": "date"}).passes()
        assert self._v({"d": "not-a-date"}, {"d": "date"}).fails()

    def test_date_format(self):
        assert self._v({"d": "2024-01"}, {"d": "date_format:%Y-%m"}).passes()
        assert self._v({"d": "01/2024"}, {"d": "date_format:%Y-%m"}).fails()

    def test_before_date(self):
        assert self._v({"d": "2023-01-01"}, {"d": "before:2024-01-01"}).passes()
        assert self._v({"d": "2025-01-01"}, {"d": "before:2024-01-01"}).fails()

    def test_after_date(self):
        assert self._v({"d": "2025-01-01"}, {"d": "after:2024-01-01"}).passes()
        assert self._v({"d": "2023-01-01"}, {"d": "after:2024-01-01"}).fails()

    def test_class_based_custom_rule(self):
        from hunt.validation.validator import Validator

        class MustBeFoo:
            def passes(self, field, value):
                return value == "foo"
            def message(self):
                return "The :attribute must be foo."

        v = Validator.make({"x": "bar"}, {"x": [MustBeFoo()]})
        v._run()
        assert "x" in v._errors
        assert "x" in v._errors["x"][0]

    def test_custom_rule_passes(self):
        from hunt.validation.validator import Validator

        class AlwaysPass:
            def passes(self, field, value):
                return True
            def message(self):
                return "never"

        v = Validator.make({"x": "anything"}, {"x": [AlwaysPass()]})
        assert v.passes()


# ===========================================================================
# A7 — Str utility gaps
# ===========================================================================

class TestStrGaps:
    def test_headline(self):
        from hunt.support.str import Str
        assert Str.headline("foo_bar_baz") == "Foo Bar Baz"
        assert Str.headline("fooBarBaz") == "Foo Bar Baz"
        assert Str.headline("foo-bar-baz") == "Foo Bar Baz"

    def test_limit(self):
        from hunt.support.str import Str
        assert Str.limit("Hello world", 5) == "Hello..."
        assert Str.limit("Hi", 10) == "Hi"
        assert Str.limit("Hello world", 5, " (more)") == "Hello (more)"

    def test_words(self):
        from hunt.support.str import Str
        assert Str.words("one two three four", 2) == "one two..."
        assert Str.words("one two", 5) == "one two"

    def test_after(self):
        from hunt.support.str import Str
        assert Str.after("foo@bar.com", "@") == "bar.com"
        assert Str.after("no-match", "@") == "no-match"

    def test_after_last(self):
        from hunt.support.str import Str
        assert Str.after_last("path/to/file.txt", "/") == "file.txt"

    def test_before(self):
        from hunt.support.str import Str
        assert Str.before("foo@bar.com", "@") == "foo"
        assert Str.before("no-match", "@") == "no-match"

    def test_before_last(self):
        from hunt.support.str import Str
        assert Str.before_last("path/to/file.txt", "/") == "path/to"

    def test_between(self):
        from hunt.support.str import Str
        assert Str.between("[hello]", "[", "]") == "hello"

    def test_squish(self):
        from hunt.support.str import Str
        assert Str.squish("  hello   world  ") == "hello world"
        assert Str.squish("foo\n\nbar") == "foo bar"

    def test_wrap(self):
        from hunt.support.str import Str
        assert Str.wrap("hello", '"') == '"hello"'
        assert Str.wrap("hello", "<b>", "</b>") == "<b>hello</b>"

    def test_is_(self):
        from hunt.support.str import Str
        assert Str.is_("foo*", "foobar") is True
        assert Str.is_("foo*", "barfoo") is False
        assert Str.is_("*.txt", "file.txt") is True
        assert Str.is_("exact", "exact") is True

    def test_replace_first(self):
        from hunt.support.str import Str
        assert Str.replace_first("a", "x", "a b a") == "x b a"

    def test_replace_last(self):
        from hunt.support.str import Str
        assert Str.replace_last("a", "x", "a b a") == "a b x"

    def test_uuid_is_valid_uuid4(self):
        import uuid
        from hunt.support.str import Str
        val = Str.uuid()
        parsed = uuid.UUID(val)
        assert parsed.version == 4

    def test_random_length(self):
        from hunt.support.str import Str
        r = Str.random(24)
        assert len(r) == 24

    def test_random_different_each_call(self):
        from hunt.support.str import Str
        assert Str.random() != Str.random()
