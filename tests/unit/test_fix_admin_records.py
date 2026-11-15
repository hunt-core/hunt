"""Regression tests for how the admin panel reads and writes records.

ADM-03 (passwords), ADM-04 / ADM-L13 (grouped, escaped search), ADM-07
(field toggle and non-fillable fields), ADM-11 (audit rows), ADM-12 (pivot
sync), ADM-13 (form value conversion), ADM-17 (links keep filters) and the
related low-severity items ADM-L7 to ADM-L11 and ADM-L14.
"""

# ruff: noqa: RUF012  (test models declare fillable/hidden as plain class attributes)
from __future__ import annotations

import datetime
import json
import logging
import re

import pytest
from sqlalchemy import event

from hunt.admin import AdminResource, AuditLog, BulkDeleteAction, ExportCsvAction, SelectFilter, TrashedFilter
from hunt.admin.fields import (
    BelongsToMany,
    Boolean,
    Currency,
    Date,
    DateTime,
    HasMany,
    Image,
    Number,
    Password,
    Select,
    Text,
)
from hunt.database.model import Model
from hunt.database.soft_deletes import SoftDeletes
from hunt.http.router import Router
from tests.unit.test_fix_admin_support import (  # noqa: F401  (fixtures)
    CSRF,
    FakeSession,
    admin,
    admin_db,
    body_of,
    json_of,
    make_kernel,
    make_request,
    rows,
    run_sql,
    send,
)

P = "/hunt-admin"


class Member(Model):
    table = "fr_members"
    fillable = ["name", "email", "password"]  # is_admin is deliberately not fillable
    hidden = ["password", "remember_token"]
    timestamps = False

    def groups(self):
        return self.belongs_to_many(Group, "fr_group_member", "member_id", "group_id")


class Group(Model):
    table = "fr_groups"
    fillable = ["name"]
    timestamps = False


class BlogPost(SoftDeletes, Model):
    table = "fr_blog_posts"
    fillable = ["title", "body", "status", "member_id"]
    timestamps = False


class StatusFilter(SelectFilter):
    name = "Status"
    attribute = "status"

    def options(self):
        return [{"label": "Draft", "value": "draft"}, {"label": "Published", "value": "published"}]


class GroupResource(AdminResource):
    model = Group
    search_columns = ["name"]

    def fields(self):
        return [Text("Name", attribute="name")]

    def can_view_any(self, request):
        return True


class BlogPostResource(AdminResource):
    model = BlogPost
    search_columns = ["title", "body"]
    per_page = 15

    def fields(self):
        return [Text("Title", attribute="title").sortable(), Text("Body", attribute="body"), Text("Status", "status")]

    def filters(self):
        return [StatusFilter(), TrashedFilter()]

    def can_view_any(self, request):
        return True


class MemberResource(AdminResource, AuditLog):
    model = Member
    search_columns = ["name", "email"]

    def fields(self):
        return [
            Text("Name", attribute="name").rules("required").sortable(),
            Text("Email", attribute="email"),
            Password("Password", attribute="password").rules("min:8"),
            Boolean("Admin", attribute="is_admin"),
            Boolean("Locked", attribute="is_locked").readonly(),
            Boolean("Verified", attribute="is_verified").hide_from_edit(),
            BelongsToMany("Groups", GroupResource, "groups"),
            HasMany("Blog Posts", BlogPostResource),
        ]

    def actions(self):
        return [BulkDeleteAction(), ExportCsvAction()]

    def can_view_any(self, request):
        return True

    def can_create(self, request):
        return True

    def can_update(self, request, instance=None):
        return True

    def can_delete(self, request, instance=None):
        return True


@pytest.fixture
def app(admin, admin_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    run_sql(
        "CREATE TABLE fr_members (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT, password TEXT,"
        " remember_token TEXT, is_admin INTEGER DEFAULT 0, is_locked INTEGER DEFAULT 0, is_verified INTEGER DEFAULT 0)"
    )
    run_sql("CREATE TABLE fr_groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    run_sql("CREATE TABLE fr_group_member (member_id INTEGER, group_id INTEGER)")
    run_sql(
        "CREATE TABLE fr_blog_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT, status TEXT,"
        " member_id INTEGER, deleted_at INTEGER)"
    )
    run_sql("INSERT INTO fr_groups (name) VALUES ('staff'), ('editors'), ('owners')")
    run_sql(
        "INSERT INTO fr_members (name, email, password, remember_token) VALUES"
        " ('Ada', 'ada@example.com', 'old-hash', 'tok-1'), ('Grace', 'grace@example.com', 'old-hash-2', 'tok-2')"
    )
    run_sql("INSERT INTO fr_group_member (member_id, group_id) VALUES (1, 1), (1, 2)")
    run_sql(
        "INSERT INTO fr_blog_posts (title, body, status, member_id, deleted_at) VALUES"
        " ('needle one', 'x', 'draft', 1, NULL),"
        " ('other', 'needle in body', 'published', 1, NULL),"
        " ('needle two', 'x', 'draft', 2, NULL),"
        " ('plain', 'needle trashed', 'draft', 1, 100)"
    )
    admin.resource(MemberResource)
    admin.resource(GroupResource)
    admin.resource(BlogPostResource)
    admin.gate(lambda request: True)
    router = Router()
    admin.register_to(router)
    session = FakeSession()
    return {"admin": admin, "router": router, "session": session, "kernel": make_kernel(router, session)}


def post(path, form=None, **kw):
    return make_request("POST", path, form={"_token": CSRF, **(form or {})}, **kw)


def post_form(path, fields=None, **kw):
    """POST as multipart/form-data, like the real admin forms (blank fields are sent)."""
    return make_request("POST", path, multipart={"_token": CSRF, **(fields or {})}, **kw)


def member(member_id):
    return rows("SELECT * FROM fr_members WHERE id = :id", {"id": member_id})[0]


def pivots(member_id=1):
    return sorted(
        r["group_id"] for r in rows("SELECT group_id FROM fr_group_member WHERE member_id = :m", {"m": member_id})
    )


def audit_rows():
    return rows("SELECT * FROM admin_audit_logs ORDER BY created_at, action")


# ---------------------------------------------------------------------------
# ADM-03 — Password fields are hashed, blank keeps the hash, never audited
# ---------------------------------------------------------------------------


class TestPasswordField:
    async def test_created_password_is_hashed_and_verifies(self, app):
        from hunt.auth.manager import verify_password

        resp = await send(
            app["kernel"],
            post(f"{P}/resources/members", {"name": "Lin", "email": "lin@example.com", "password": "correct horse"}),
        )
        assert resp.status == 302
        row = rows("SELECT * FROM fr_members WHERE name = 'Lin'")[0]
        assert row["password"] != "correct horse"
        assert verify_password("correct horse", row["password"]) is True

    async def test_blank_password_on_edit_keeps_the_stored_hash(self, app):
        resp = await send(app["kernel"], post_form(f"{P}/resources/members/1", {"name": "Ada L", "password": ""}))
        assert resp.status == 302
        assert member(1)["name"] == "Ada L"
        assert member(1)["password"] == "old-hash"

    async def test_blank_password_on_edit_skips_its_rules(self, app):
        # "min:8" (or "required") must not reject the blank that means "keep the current password".
        resp = await send(
            app["kernel"], post_form(f"{P}/resources/members/1", {"name": "Ada L", "password": ""}, expects_json=True)
        )
        assert resp.status == 302

    async def test_new_password_on_edit_is_hashed(self, app):
        from hunt.auth.manager import verify_password

        await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada", "password": "brand new pw"}))
        assert verify_password("brand new pw", member(1)["password"]) is True

    async def test_validation_runs_on_the_plain_value(self, app):
        resp = await send(
            app["kernel"],
            post(f"{P}/resources/members", {"name": "Short", "password": "abc"}, expects_json=True),
        )
        assert resp.status == 422
        assert rows("SELECT id FROM fr_members WHERE name = 'Short'") == []

    async def test_audit_rows_contain_no_password_value(self, app):
        await send(
            app["kernel"],
            post(f"{P}/resources/members", {"name": "Lin", "email": "lin@example.com", "password": "correct horse"}),
        )
        await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada", "password": "another secret"}))
        dump = json.dumps(audit_rows())
        assert len(audit_rows()) == 2
        assert "correct horse" not in dump and "another secret" not in dump
        assert "old-hash" not in dump
        assert "$2" not in dump  # no bcrypt hash either
        assert "password" not in dump

    def test_prepare_for_storage_hook(self):
        from hunt.admin.field import SKIP, Field

        assert Field("X").prepare_for_storage("v", is_update=False) == "v"
        field = Password("Password")
        assert field.prepare_for_storage("", is_update=True) is SKIP
        assert field.prepare_for_storage(None, is_update=False) is SKIP
        from hunt.auth.manager import verify_password

        stored = field.prepare_for_storage("s3cret-pw", is_update=True)
        assert stored != "s3cret-pw"
        assert verify_password("s3cret-pw", stored) is True

    async def test_edit_form_never_echoes_the_hash(self, app):
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/members/1/edit"))
        assert resp.status == 200
        assert "old-hash" not in body_of(resp)


# ---------------------------------------------------------------------------
# ADM-04 / ADM-L13 — search is one AND-ed, escaped group
# ---------------------------------------------------------------------------


class TestSearchGrouping:
    def _sql_for(self, admin_db, query):  # noqa: F811
        statements: list[str] = []
        engine = admin_db.connection()

        def _capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(" ".join(statement.split()))

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            query.get()
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
        return statements[-1]

    def test_generated_sql_groups_the_search(self, app, admin_db):  # noqa: F811
        resource = BlogPostResource()
        query = resource._search_group(BlogPost.query().where("status", "draft"), "needle")
        sql = self._sql_for(admin_db, query)
        # ... AND (title LIKE ? OR body LIKE ?): the OR never reaches the top level.
        assert re.search(r"AND \(\(?[\w.]*title\b[^()]*LIKE[^()]*OR [\w.(]*body\b[^()]*LIKE[^()]*\)", sql, re.I), sql
        where = sql.upper().split(" WHERE ", 1)[1]
        depth = 0
        for token in re.findall(r"\(|\)|\bOR\b", where):
            depth += 1 if token == "(" else -1 if token == ")" else 0
            assert not (token == "OR" and depth == 0), f"top-level OR in: {sql}"

    async def test_filter_plus_search(self, app):
        resp = await send(
            app["kernel"],
            make_request("GET", f"{P}/resources/blog_posts", query={"search": "needle", "filter_status": "draft"}),
        )
        html = body_of(resp)
        assert "needle one" in html and "needle two" in html
        assert "needle in body" not in html and ">other<" not in html  # published row, matched on 2nd column

    async def test_search_does_not_resurrect_soft_deleted_rows(self, app):
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/blog_posts", query={"search": "needle"}))
        html = body_of(resp)
        assert "needle one" in html and "other" in html
        assert "plain" not in html  # soft-deleted, matches on the second search column

    async def test_has_many_panel_search_stays_inside_the_parent(self, app):
        resp = await send(
            app["kernel"],
            make_request("GET", f"{P}/resources/members/1", query={"rel_blog_posts_search": "needle"}),
        )
        html = body_of(resp)
        assert resp.status == 200
        assert "needle one" in html and "other" in html
        assert "needle two" not in html  # belongs to member 2

    async def test_global_search_is_grouped_and_escaped(self, app):
        run_sql("INSERT INTO fr_blog_posts (title, body, status, member_id) VALUES ('100% sure', 'x', 'draft', 1)")
        resp = await send(app["kernel"], make_request("GET", f"{P}/search", query={"q": "needle"}))
        titles = [i["title"] for g in json_of(resp)["groups"] if g["resource"] == "BlogPost" for i in g["items"]]
        assert "plain" not in titles and len(titles) == 3

        # A bare wildcard is a literal, not "match everything".
        resp = await send(app["kernel"], make_request("GET", f"{P}/search", query={"q": "%"}))
        titles = [i["title"] for g in json_of(resp)["groups"] if g["resource"] == "BlogPost" for i in g["items"]]
        assert titles == ["100% sure"]

    async def test_index_search_treats_wildcards_literally(self, app):
        run_sql("INSERT INTO fr_blog_posts (title, body, status, member_id) VALUES ('snake_case', 'x', 'draft', 1)")
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/blog_posts", query={"search": "_"}))
        html = body_of(resp)
        assert "snake_case" in html
        assert "needle one" not in html

    def test_fallback_without_where_like_still_groups(self):
        from unittest.mock import MagicMock

        captured = {}
        query = MagicMock()

        def where_group(callback):
            inner = MagicMock()
            inner.where.return_value = inner
            inner.or_where.return_value = inner
            callback(inner)
            captured["inner"] = inner
            return "grouped"

        query.where_group.side_effect = where_group
        assert BlogPostResource()._search_group(query, "50%_a\\b") == "grouped"
        inner = captured["inner"]
        inner.where.assert_called_once_with("title", "LIKE", "%50\\%\\_a\\\\b%")
        inner.or_where.assert_called_once_with("body", "LIKE", "%50\\%\\_a\\\\b%")
        query.where.assert_not_called()
        query.or_where.assert_not_called()


# ---------------------------------------------------------------------------
# ADM-07 — field toggle and non-fillable declared fields
# ---------------------------------------------------------------------------


class TestFieldToggleAndForceFill:
    def _toggle(self, attribute, member_id=1):
        return make_request(
            "POST",
            f"{P}/resources/members/{member_id}/field-toggle",
            json_body={"attribute": attribute},
            headers={"X-CSRF-TOKEN": CSRF},
        )

    async def test_toggle_persists_a_non_fillable_boolean(self, app):
        resp = await send(app["kernel"], self._toggle("is_admin"))
        assert resp.status == 200
        assert json_of(resp) == {"value": True}
        assert member(1)["is_admin"] == 1  # really saved, not just reported

    async def test_readonly_boolean_cannot_be_toggled(self, app):
        resp = await send(app["kernel"], self._toggle("is_locked"))
        assert resp.status == 400
        assert member(1)["is_locked"] == 0

    async def test_boolean_hidden_from_edit_cannot_be_toggled(self, app):
        resp = await send(app["kernel"], self._toggle("is_verified"))
        assert resp.status == 400
        assert member(1)["is_verified"] == 0

    async def test_non_boolean_attribute_rejected(self, app):
        assert (await send(app["kernel"], self._toggle("name"))).status == 400

    async def test_toggle_is_audited(self, app):
        await send(app["kernel"], self._toggle("is_admin"))
        entries = audit_rows()
        assert [e["action"] for e in entries] == ["toggle"]
        assert json.loads(entries[0]["diff"])["is_admin"]["new"] is True

    async def test_index_only_renders_toggle_for_editable_booleans(self, app):
        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/members")))
        assert 'data-attribute="is_admin"' in html
        assert 'data-attribute="is_locked"' not in html
        assert 'data-attribute="is_verified"' not in html

    async def test_store_saves_declared_non_fillable_field(self, app):
        await send(app["kernel"], post(f"{P}/resources/members", {"name": "Root", "is_admin": "1"}))
        row = rows("SELECT * FROM fr_members WHERE name = 'Root'")[0]
        assert row["is_admin"] == 1

    async def test_update_saves_declared_non_fillable_field(self, app):
        await send(app["kernel"], post(f"{P}/resources/members/2", {"name": "Grace", "is_admin": "1"}))
        assert member(2)["is_admin"] == 1
        # an unchecked box (absent from the POST) clears it again
        await send(app["kernel"], post(f"{P}/resources/members/2", {"name": "Grace"}))
        assert member(2)["is_admin"] == 0

    async def test_edit_form_method_field_reaches_update_and_destroy(self, app):
        # The edit form posts _method=PUT; whether or not the kernel rewrites
        # the method, the request must land on update (and DELETE on destroy).
        resp = await send(
            app["kernel"], post_form(f"{P}/resources/members/2", {"_method": "PUT", "name": "Grace H", "is_admin": "1"})
        )
        assert resp.status == 302
        assert member(2)["name"] == "Grace H" and member(2)["is_admin"] == 1

        resp = await send(app["kernel"], post(f"{P}/resources/members/2", {"_method": "DELETE"}))
        assert resp.status == 302
        assert rows("SELECT id FROM fr_members WHERE id = 2") == []

    async def test_regex_rule_followed_by_other_rules(self, app, monkeypatch):
        # Rules reach the validator as a list, so a regex containing "|" keeps
        # its pattern and the rules after it still apply.
        original = MemberResource.fields

        def fields(self):
            return [Text("Name", attribute="name").rules("required", "regex:^(ada|lin)$", "max:3"), *original(self)[1:]]

        monkeypatch.setattr(MemberResource, "fields", fields)
        ok = await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "lin"}, expects_json=True))
        assert ok.status == 302 and member(1)["name"] == "lin"
        bad = await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "bob"}, expects_json=True))
        assert bad.status == 422 and member(1)["name"] == "lin"

    async def test_undeclared_and_readonly_attributes_are_never_written(self, app):
        await send(
            app["kernel"],
            post(f"{P}/resources/members/1", {"name": "Ada", "remember_token": "stolen", "is_locked": "1", "id": "99"}),
        )
        row = member(1)
        assert row["remember_token"] == "tok-1"
        assert row["is_locked"] == 0


# ---------------------------------------------------------------------------
# ADM-11 — audit rows: complete, sanitised, failures visible
# ---------------------------------------------------------------------------


class TestAuditLog:
    async def test_hidden_attributes_are_stripped(self, app):
        await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada Lovelace"}))
        await send(app["kernel"], post(f"{P}/resources/members/2/delete"))
        dump = json.dumps(audit_rows())
        assert "tok-1" not in dump and "tok-2" not in dump
        assert "old-hash" not in dump
        assert "remember_token" not in dump
        assert "Ada Lovelace" in dump

    async def test_bulk_action_writes_one_row_per_record(self, app):
        await send(app["kernel"], post(f"{P}/resources/members/actions/delete_selected", {"ids": "1,2"}))
        entries = audit_rows()
        assert sorted(e["record_id"] for e in entries) == ["1", "2"]
        assert {e["action"] for e in entries} == {"action:delete_selected"}
        assert "old-hash" not in json.dumps(entries)

    async def test_pivot_changes_are_audited(self, app):
        await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada", "groups": "1,3"}))
        diff = json.loads(audit_rows()[0]["diff"])
        assert diff["groups"] == {"old": ["1", "2"], "new": ["1", "3"]}

    async def test_history_panel_shows_no_secret(self, app):
        await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada L", "password": "new password"}))
        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/members/1")))
        assert "History" in html and "Ada L" in html
        assert "old-hash" not in html and "new password" not in html and "$2b$" not in html

    def test_write_failure_is_logged_not_swallowed(self, app, monkeypatch, caplog):
        from hunt.admin import audit

        def boom(row):
            raise RuntimeError("audit table gone")

        monkeypatch.setattr(audit, "_insert_audit_row", boom)
        with caplog.at_level(logging.ERROR, logger="hunt.admin.audit"):
            audit._write_audit(1, "MemberResource", 1, "update", {}, {})
        assert any("audit log write failed" in r.getMessage() for r in caplog.records)

    def test_strict_mode_fails_the_request(self, app, monkeypatch):
        from hunt.admin import audit

        def boom(row):
            raise RuntimeError("audit table gone")

        monkeypatch.setattr(audit, "_insert_audit_row", boom)
        app["admin"].audit_strict = True
        with pytest.raises(RuntimeError):
            audit._write_audit(1, "MemberResource", 1, "update", {}, {})

    async def test_tool_operations_are_audited(self, app):
        from hunt.cache.manager import Cache

        app["admin"].authorize_tool("cache")
        Cache.configure("array")
        Cache._get_store().put("greeting", "hi", 60)
        await send(app["kernel"], post(f"{P}/cache/delete", {"key": "greeting"}))
        entries = [e for e in audit_rows() if e["resource_type"] == "tool:cache"]
        assert [(e["action"], e["record_id"]) for e in entries] == [("delete", "greeting")]


# ---------------------------------------------------------------------------
# ADM-12 — BelongsToMany sync is a validated, atomic diff
# ---------------------------------------------------------------------------


class TestBelongsToManySync:
    async def test_sync_applies_the_difference(self, app):
        resp = await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada", "groups": "2,3"}))
        assert resp.status == 302
        assert pivots() == [2, 3]

    async def test_absent_input_leaves_pivots_untouched(self, app):
        await send(app["kernel"], post(f"{P}/resources/members/1", {"name": "Ada"}))
        assert pivots() == [1, 2]

    async def test_empty_input_clears_pivots(self, app):
        await send(app["kernel"], post_form(f"{P}/resources/members/1", {"name": "Ada", "groups": ""}))
        assert pivots() == []

    async def test_invalid_id_is_a_validation_error_and_changes_nothing(self, app):
        resp = await send(
            app["kernel"],
            post(f"{P}/resources/members/1", {"name": "Renamed", "groups": "2,999"}, expects_json=True),
        )
        assert resp.status == 422
        assert "groups" in json_of(resp)["errors"]
        assert pivots() == [1, 2]
        assert member(1)["name"] == "Ada"

    async def test_non_numeric_id_does_not_wipe_pivots(self, app):
        resp = await send(
            app["kernel"],
            post(f"{P}/resources/members/1", {"name": "Ada", "groups": "abc-uuid"}, expects_json=True),
        )
        assert resp.status == 422
        assert pivots() == [1, 2]

    async def test_failure_part_way_rolls_everything_back(self, app, monkeypatch):
        from hunt.database.relations.belongs_to_many import BelongsToMany as Relation

        def failing_attach(self, related_id, pivot_data=None):
            raise RuntimeError("pivot insert failed")

        monkeypatch.setattr(Relation, "attach", failing_attach)
        resp = await send(
            app["kernel"],
            post(f"{P}/resources/members/1", {"name": "Renamed", "groups": "3"}, expects_json=True),
        )
        assert resp.status == 422
        # The two detaches and the record update that preceded the failure are rolled back.
        assert pivots() == [1, 2]
        assert member(1)["name"] == "Ada"
        assert "admin_success" not in app["session"].flashed

    async def test_create_with_pivots(self, app):
        await send(app["kernel"], post(f"{P}/resources/members", {"name": "Lin", "groups": "1,3"}))
        new_id = rows("SELECT id FROM fr_members WHERE name = 'Lin'")[0]["id"]
        assert pivots(new_id) == [1, 3]


# ---------------------------------------------------------------------------
# ADM-13 — form values are converted to and from storage format
# ---------------------------------------------------------------------------


class TestFormValueConversion:
    def test_datetime_epoch_round_trip(self):
        field = DateTime("Published At")
        epoch = int(datetime.datetime(2026, 11, 15, 10, 30).timestamp())
        assert field.to_form_value(epoch) == "2026-11-15T10:30"
        # the column held an epoch, so an epoch goes back
        assert field.from_form_value("2026-11-15T10:30", epoch) == epoch

    def test_datetime_string_and_datetime_storage(self):
        field = DateTime("Published At")
        assert field.to_form_value("2026-11-15 10:30:00") == "2026-11-15T10:30"
        assert field.from_form_value("2026-11-15T10:30", "2026-01-01 00:00:00") == "2026-11-15 10:30:00"
        assert field.from_form_value("2026-11-15T10:30") == "2026-11-15 10:30:00"
        current = datetime.datetime(2026, 1, 1)
        assert field.from_form_value("2026-11-15T10:30", current) == datetime.datetime(2026, 11, 15, 10, 30)

    def test_datetime_as_timestamp(self):
        field = DateTime("Published At").as_timestamp()
        value = field.from_form_value("2026-11-15T10:30")
        assert value == int(datetime.datetime(2026, 11, 15, 10, 30).timestamp())
        assert field.to_form_value(value) == "2026-11-15T10:30"

    def test_blank_and_null_values(self):
        assert DateTime("X").from_form_value("") is None
        assert DateTime("X").to_form_value(None) == ""
        assert Date("X").from_form_value("") is None
        assert Number("X").from_form_value("") is None
        assert Currency("X").from_form_value("") is None
        assert Select("X", ["a"]).nullable().from_form_value("", "a") is None
        assert Select("X", ["a"]).from_form_value("", "a") == ""
        assert Text("X").nullable().from_form_value("") is None
        assert Text("X").from_form_value("") == ""
        assert Text("X").to_form_value(None) == ""

    def test_date_round_trip(self):
        field = Date("Born")
        assert field.to_form_value("2026-11-15") == "2026-11-15"
        assert field.to_form_value(datetime.date(2026, 11, 15)) == "2026-11-15"
        assert field.from_form_value("2026-11-15") == "2026-11-15"
        assert field.from_form_value("2026-11-15", datetime.date(2020, 1, 1)) == datetime.date(2026, 11, 15)

    def test_number_and_boolean_types(self):
        assert Number("N").from_form_value("42") == 42
        assert Number("N").from_form_value("4.5") == 4.5
        assert Boolean("B").from_form_value("1") is True
        assert Boolean("B").from_form_value(False) is False

    def test_to_form_value_is_idempotent_on_old_input(self):
        field = DateTime("X")
        assert field.to_form_value("2026-11-15T10:30") == "2026-11-15T10:30"

    async def test_edit_form_renders_epoch_as_datetime_local(self, app, admin):  # noqa: F811
        class Event(Model):
            table = "fr_events"
            fillable = ["name"]
            timestamps = False

        class EventResource(AdminResource):
            model = Event

            def fields(self):
                return [Text("Name", attribute="name"), DateTime("Starts At", attribute="starts_at"), Number("Seats")]

            def can_view_any(self, request):
                return True

            def can_update(self, request, instance=None):
                return True

        epoch = int(datetime.datetime(2026, 11, 15, 10, 30).timestamp())
        run_sql("CREATE TABLE fr_events (id INTEGER PRIMARY KEY, name TEXT, starts_at INTEGER, seats INTEGER)")
        run_sql("INSERT INTO fr_events (id, name, starts_at, seats) VALUES (1, 'Launch', :ts, NULL)", {"ts": epoch})
        admin.resource(EventResource)

        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/events/1/edit")))
        assert 'value="2026-11-15T10:30"' in html
        assert 'value="None"' not in html

        await send(
            app["kernel"],
            post_form(f"{P}/resources/events/1", {"name": "Launch", "starts_at": "2026-12-01T09:00", "seats": ""}),
        )
        row = rows("SELECT * FROM fr_events WHERE id = 1")[0]
        assert row["starts_at"] == int(datetime.datetime(2026, 12, 1, 9, 0).timestamp())
        assert row["seats"] is None  # blank number is NULL, not ''

        await send(
            app["kernel"], post_form(f"{P}/resources/events/1", {"name": "Launch", "starts_at": "", "seats": "12"})
        )
        row = rows("SELECT * FROM fr_events WHERE id = 1")[0]
        assert row["starts_at"] is None
        assert row["seats"] == 12


# ---------------------------------------------------------------------------
# ADM-17 / ADM-L9 / ADM-L10 / ADM-L11 — index links, sorting, paging
# ---------------------------------------------------------------------------


class TestIndexLinksSortingPaging:
    @pytest.fixture
    def many(self, app):
        for i in range(40):
            run_sql(
                "INSERT INTO fr_blog_posts (title, body, status, member_id) VALUES (:t, 'b', 'draft', 1)",
                {"t": f"bulk {i:02d} a&b"},
            )
        return app

    async def test_pagination_links_keep_filter_and_encode_search(self, many):
        resp = await send(
            many["kernel"],
            make_request(
                "GET",
                f"{P}/resources/blog_posts",
                query={"search": "a&b", "filter_status": "draft", "per_page": "10", "sort": "title", "dir": "asc"},
            ),
        )
        html = body_of(resp)
        hrefs = re.findall(r'href="([^"]*[?&amp;]page=2[^"]*)"', html)
        assert hrefs, "expected a link to page 2"
        for href in hrefs:
            assert "filter_status=draft" in href
            assert "search=a%26b" in href  # the & inside the search term is encoded
            assert "sort=title" in href and "dir=asc" in href
            assert "per_page=10" in href

    async def test_page_two_applies_the_filter(self, many):
        run_sql("INSERT INTO fr_blog_posts (title, body, status, member_id) VALUES ('pub a&b', 'b', 'published', 1)")
        resp = await send(
            many["kernel"],
            make_request(
                "GET",
                f"{P}/resources/blog_posts",
                query={"search": "a&b", "filter_status": "draft", "per_page": "10", "page": "2"},
            ),
        )
        html = body_of(resp)
        assert "pub a&amp;b" not in html
        assert "bulk" in html

    async def test_sort_links_keep_the_filter(self, many):
        html = body_of(
            await send(
                many["kernel"],
                make_request("GET", f"{P}/resources/blog_posts", query={"filter_status": "draft"}),
            )
        )
        sort_links = [h for h in re.findall(r'href="([^"]*)"', html) if "sort=title" in h]
        assert sort_links and all("filter_status=draft" in h for h in sort_links)

    def test_qs_helper(self):
        from hunt.admin.application import _qs

        assert _qs({"search": "a&b=c", "page": 1}, page=2) == "search=a%26b%3Dc&page=2"
        assert _qs({"search": "", "x": None, "y": 0}) == "y=0"

    @pytest.mark.parametrize("page", ["0", "-5", "abc", "9999"])
    async def test_out_of_range_page_is_clamped(self, many, page):
        resp = await send(many["kernel"], make_request("GET", f"{P}/resources/blog_posts", query={"page": page}))
        assert resp.status == 200
        assert "bulk" in body_of(resp)

    async def test_configured_per_page_is_honoured(self, many):
        html = body_of(await send(many["kernel"], make_request("GET", f"{P}/resources/blog_posts")))
        assert re.search(r'<option value="15"\s+selected', html)
        assert "per_page=15" in html
        assert len(re.findall(r"bulk \d\d", html)) == 15

    def test_only_sortable_fields_can_be_sorted(self):
        cols = MemberResource()._sortable_columns()
        assert cols == {"name", "id"}
        assert "password" not in cols and "groups" not in cols and "blog_posts" not in cols

    async def test_sorting_by_an_unsortable_or_relation_attribute_is_ignored(self, app):
        for attribute in ("password", "groups", "blog_posts", "email"):
            resp = await send(
                app["kernel"], make_request("GET", f"{P}/resources/members", query={"sort": attribute, "dir": "asc"})
            )
            assert resp.status == 200


# ---------------------------------------------------------------------------
# ADM-L7 / ADM-L8 / ADM-L14
# ---------------------------------------------------------------------------


class TestLowSeverityRecordItems:
    def test_image_rules_keep_the_default_mimes_rule(self):
        field = Image("Avatar").rules("required")
        assert "required" in field._rules
        assert "mimes:jpg,jpeg,png,gif,webp" in field._rules

    def test_image_rules_can_replace_mimes_explicitly(self):
        field = Image("Avatar").rules("required", "mimes:png")
        assert field._rules == ["required", "mimes:png"]

    async def test_csv_export_omits_hidden_columns(self, app):
        resp = await send(app["kernel"], post(f"{P}/resources/members/actions/export_csv", {"ids": "1,2"}))
        csv_text = body_of(resp)
        header = csv_text.splitlines()[0].split(",")
        assert "password" not in header and "remember_token" not in header
        assert "name" in header and "email" in header
        assert "old-hash" not in csv_text and "tok-1" not in csv_text

    async def test_csv_export_neutralises_formula_cells(self, app):
        run_sql("UPDATE fr_members SET name = '=HYPERLINK(\"http://x\")', email = '@SUM(1)' WHERE id = 1")
        run_sql("UPDATE fr_members SET name = '+1', email = '-2' WHERE id = 2")
        resp = await send(app["kernel"], post(f"{P}/resources/members/actions/export_csv", {"ids": "1,2"}))
        import csv
        import io

        records = list(csv.DictReader(io.StringIO(body_of(resp))))
        for record in records:
            assert record["name"].startswith("'")
            assert record["email"].startswith("'")

    def test_export_without_a_resource_still_drops_model_hidden(self):
        instance = Member.__new__(Member)
        instance._attributes = {"id": 1, "name": "Ada", "password": "h", "remember_token": "t"}
        result = ExportCsvAction().handle(None, [instance])
        assert result.download_content.splitlines()[0] == "id,name"

    async def test_has_many_foreign_key_uses_snake_case(self, app, admin):  # noqa: F811
        class PostComment(Model):
            table = "fr_post_comments"
            fillable = ["text"]
            timestamps = False

        class PostCommentResource(AdminResource):
            model = PostComment

            def fields(self):
                return [Text("Text", attribute="text")]

            def can_view_any(self, request):
                return True

        class BlogPostWithComments(BlogPostResource):
            def fields(self):
                return [*super().fields(), HasMany("Comments", PostCommentResource)]

        run_sql("CREATE TABLE fr_post_comments (id INTEGER PRIMARY KEY, blog_post_id INTEGER, text TEXT)")
        run_sql("INSERT INTO fr_post_comments (blog_post_id, text) VALUES (1, 'first comment'), (2, 'elsewhere')")
        admin._resources = [r for r in admin._resources if r is not BlogPostResource]
        admin.resource(BlogPostWithComments)
        admin.resource(PostCommentResource)

        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/blog_posts/1")))
        # BlogPost -> blog_post_id (the old guess was "blogpost_id", which silently showed nothing)
        assert "first comment" in html
        assert "elsewhere" not in html

    def test_currency_honours_the_configured_code(self):
        class Row:
            _attributes = {"price": "12.5"}

        assert Currency("Price").display_value(Row()) == "$12.50"
        assert Currency("Price").currency("EUR").display_value(Row()) == "€12.50"
        assert Currency("Price").currency("JPY").display_value(Row()) == "¥12"
        assert Currency("Price").currency("XTS").display_value(Row()) == "XTS 12.50"
