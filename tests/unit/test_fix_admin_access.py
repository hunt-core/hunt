"""Regression tests for the admin access-control findings.

ADM-01 (session ids), ADM-02 (bulk action permissions), ADM-05 (search and
panels honour can_view_any), ADM-06 (route shadowing), ADM-08 (async gate and
policies), ADM-09 (tool authorization), ADM-10 (CSRF on the admin group) and
ADM-16 (bounded action scope, restore).
"""

# ruff: noqa: RUF012  (test models declare fillable/hidden as plain class attributes)
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event

from hunt.admin import AdminResource, BulkDeleteAction, ExportCsvAction, RestoreAction
from hunt.admin.fields import BelongsTo, BelongsToMany, HasMany, Text
from hunt.database.model import Model
from hunt.database.soft_deletes import SoftDeletes
from hunt.http.response import HttpException
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


class Author(Model):
    table = "fa_authors"
    fillable = ["name"]
    timestamps = False


class Tag(Model):
    table = "fa_tags"
    fillable = ["name"]
    timestamps = False


class Article(SoftDeletes, Model):
    table = "fa_articles"
    fillable = ["title", "status", "author_id"]
    timestamps = False

    def tags(self):
        return self.belongs_to_many(Tag, "fa_article_tag", "article_id", "tag_id")


class Policy:
    """Mutable policy switches shared by the test resources."""

    view = True
    create = True
    update = True
    delete = True
    view_authors = True
    view_tags = True
    deletable_ids: set | None = None


class AuthorResource(AdminResource):
    model = Author
    search_columns = ["name"]

    def fields(self):
        return [Text("Name", attribute="name"), HasMany("Articles", ArticleResource, foreign_key="author_id")]

    def can_view_any(self, request):
        return Policy.view_authors

    def can_update(self, request, instance=None):
        return True


class TagResource(AdminResource):
    model = Tag
    search_columns = ["name"]

    def fields(self):
        return [Text("Name", attribute="name")]

    def can_view_any(self, request):
        return Policy.view_tags


class ArticleResource(AdminResource):
    model = Article
    search_columns = ["title", "status"]

    def fields(self):
        return [
            Text("Title", attribute="title").sortable(),
            Text("Status", attribute="status"),
            BelongsTo("Author", AuthorResource, attribute="author_id").searchable(),
            BelongsToMany("Tags", TagResource, "tags"),
        ]

    def actions(self):
        return [BulkDeleteAction(), ExportCsvAction(), RestoreAction()]

    def can_view_any(self, request):
        return Policy.view

    def can_create(self, request):
        return Policy.create

    def can_update(self, request, instance=None):
        return Policy.update

    def can_delete(self, request, instance=None):
        if not Policy.delete:
            return False
        if instance is not None and Policy.deletable_ids is not None:
            return instance._attributes.get("id") in Policy.deletable_ids
        return True


@pytest.fixture
def app(admin, admin_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    for name in ("view", "create", "update", "delete", "view_authors", "view_tags"):
        setattr(Policy, name, True)
    Policy.deletable_ids = None

    run_sql("CREATE TABLE fa_authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    run_sql("CREATE TABLE fa_tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    run_sql(
        "CREATE TABLE fa_articles (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT,"
        " author_id INTEGER, deleted_at INTEGER)"
    )
    run_sql("CREATE TABLE fa_article_tag (article_id INTEGER, tag_id INTEGER)")
    run_sql("INSERT INTO fa_authors (name) VALUES ('Ada'), ('Grace')")
    run_sql("INSERT INTO fa_tags (name) VALUES ('python'), ('rust')")
    run_sql(
        "INSERT INTO fa_articles (title, status, author_id) VALUES"
        " ('Alpha', 'draft', 1), ('Beta', 'published', 1), ('Gamma', 'draft', 2)"
    )

    admin.resource(AuthorResource)
    admin.resource(TagResource)
    admin.resource(ArticleResource)
    admin.gate(lambda request: True)
    router = Router()
    admin.register_to(router)
    session = FakeSession()
    return {"admin": admin, "router": router, "session": session, "kernel": make_kernel(router, session)}


def post(path, form=None, **kw):
    data = {"_token": CSRF, **(form or {})}
    return make_request("POST", path, form=data, **kw)


def article_ids(include_trashed=False):
    sql = "SELECT id FROM fa_articles" + ("" if include_trashed else " WHERE deleted_at IS NULL") + " ORDER BY id"
    return [r["id"] for r in rows(sql)]


# ---------------------------------------------------------------------------
# ADM-01 — the sessions page never exposes or accepts the raw session id
# ---------------------------------------------------------------------------


class TestSessionsUsePublicIds:
    RAW = "s3cr3t-session-cookie-value-0123456789abcdef"

    @pytest.fixture
    def sessions(self, app, tmp_path, monkeypatch):
        from hunt.session.registry import SessionRegistry

        # The registry stores sha256(session id); revoking deletes the session
        # file of that name, so the registry needs a storage directory.
        monkeypatch.setattr("hunt.support.helpers.storage_path", lambda *parts: str(tmp_path.joinpath(*parts)))
        run_sql(
            "CREATE TABLE user_sessions (id VARCHAR(128) PRIMARY KEY, user_id VARCHAR(64), guard VARCHAR(32),"
            " ip_address VARCHAR(64), user_agent TEXT, last_active_at INTEGER)"
        )
        reg = SessionRegistry()
        reg.register(self.RAW, 7, "web", "10.0.0.1", "pytest-agent")
        app["admin"].authorize_tool("sessions")
        listed, _total = reg.all_sessions()
        app["public_id"] = listed[0]["public_id"]
        return app

    async def test_page_contains_no_session_id(self, sessions):
        resp = await send(sessions["kernel"], make_request("GET", f"{P}/sessions"))
        html = body_of(resp)
        assert resp.status == 200
        assert self.RAW not in html
        assert self.RAW not in repr(rows("SELECT id FROM user_sessions"))
        assert sessions["public_id"] in html
        assert f"{P}/sessions/{sessions['public_id']}/revoke" in html

    async def test_revoke_with_raw_session_id_is_404(self, sessions):
        resp = await send(sessions["kernel"], post(f"{P}/sessions/{self.RAW}/revoke", expects_json=True))
        assert resp.status == 404
        assert len(rows("SELECT id FROM user_sessions")) == 1

    async def test_revoke_by_public_id(self, sessions):
        resp = await send(sessions["kernel"], post(f"{P}/sessions/{sessions['public_id']}/revoke"))
        assert resp.status == 302
        assert rows("SELECT id FROM user_sessions") == []

    def test_rows_are_rebuilt_from_an_allow_list(self):
        from hunt.admin.controllers.sessions import _safe_row

        safe = _safe_row({"id": self.RAW, "public_id": "abc", "user_id": 1, "payload": "x"})
        assert "id" not in safe and "payload" not in safe
        assert self.RAW not in repr(safe)
        assert safe["public_id"] == "abc"


# ---------------------------------------------------------------------------
# ADM-02 / ADM-16 — bulk actions authorize per record and stay bounded
# ---------------------------------------------------------------------------


class TestBulkActionAuthorization:
    async def test_update_only_user_cannot_bulk_delete(self, app):
        Policy.delete = False
        resp = await send(
            app["kernel"], post(f"{P}/resources/articles/actions/delete_selected", {"ids": "1,2"}, expects_json=True)
        )
        assert resp.status == 403
        assert article_ids() == [1, 2, 3]

    async def test_per_instance_delete_policy_is_honoured(self, app):
        Policy.deletable_ids = {2}
        resp = await send(app["kernel"], post(f"{P}/resources/articles/actions/delete_selected", {"ids": "1,2,3"}))
        assert resp.status == 302
        assert article_ids() == [1, 3]

    async def test_bulk_delete_allowed_with_delete_permission(self, app):
        resp = await send(app["kernel"], post(f"{P}/resources/articles/actions/delete_selected", {"ids": "[1, 3]"}))
        assert resp.status == 302
        assert article_ids() == [2]

    async def test_export_needs_view_not_update(self, app):
        Policy.update = False
        resp = await send(app["kernel"], post(f"{P}/resources/articles/actions/export_csv", {"ids": "1"}))
        assert resp.status == 200
        assert "Alpha" in body_of(resp)

        Policy.view = False
        resp = await send(
            app["kernel"], post(f"{P}/resources/articles/actions/export_csv", {"ids": "1"}, expects_json=True)
        )
        assert resp.status == 403

    async def test_no_selection_does_not_run_the_action(self, app):
        resp = await send(app["kernel"], post(f"{P}/resources/articles/actions/delete_selected", {"ids": ""}))
        assert resp.status == 302
        assert article_ids() == [1, 2, 3]
        assert "select" in app["session"].flashed["admin_error"].lower()

    async def test_restore_action_restores_soft_deleted_records(self, app):
        run_sql("UPDATE fa_articles SET deleted_at = 100 WHERE id = 2")
        assert article_ids() == [1, 3]
        resp = await send(app["kernel"], post(f"{P}/resources/articles/actions/restore_selected", {"ids": "2"}))
        assert resp.status == 302
        assert article_ids() == [1, 2, 3]
        assert "restored" in app["session"].flashed["admin_success"]

    async def test_scope_query_is_bounded_by_the_selected_ids(self, app, admin_db):  # noqa: F811
        statements: list[str] = []
        engine = admin_db.connection()

        def _capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            resp = await send(app["kernel"], post(f"{P}/resources/articles/actions/export_csv", {"ids": "1"}))
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
        assert resp.status == 200
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "fa_articles" in s]
        assert selects, "the action should load the selected records"
        # Every read of the table is restricted to the selected ids: no full-table hydration.
        assert all(" IN (" in s.upper() for s in selects), selects
        assert "Beta" not in body_of(resp)

    async def test_too_many_ids_rejected(self, app):
        ids = ",".join(str(i) for i in range(1, 1102))
        resp = await send(
            app["kernel"], post(f"{P}/resources/articles/actions/export_csv", {"ids": ids}, expects_json=True)
        )
        assert resp.status == 422


# ---------------------------------------------------------------------------
# ADM-05 — global search, panels and relation search honour can_view_any
# ---------------------------------------------------------------------------


class TestViewPermissionOnSecondaryEndpoints:
    async def test_global_search_skips_resources_the_user_cannot_view(self, app):
        resp = await send(app["kernel"], make_request("GET", f"{P}/search", query={"q": "a"}))
        labels = {g["resource"] for g in json_of(resp)["groups"]}
        assert "Article" in labels

        Policy.view = False
        resp = await send(app["kernel"], make_request("GET", f"{P}/search", query={"q": "a"}))
        labels = {g["resource"] for g in json_of(resp)["groups"]}
        assert "Article" not in labels
        assert "Author" in labels

    async def test_has_many_panel_hidden_when_related_resource_denies_view(self, app):
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/authors/1"))
        assert resp.status == 200
        assert "Alpha" in body_of(resp)

        Policy.view = False
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/authors/1"))
        assert resp.status == 200
        assert "Alpha" not in body_of(resp)

        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/authors/1/edit"))
        assert resp.status == 200
        assert "Alpha" not in body_of(resp)

    async def test_relation_search_requires_owner_and_related_permissions(self, app):
        url = f"{P}/resources/articles/search-relation"
        ok = await send(app["kernel"], make_request("GET", url, query={"field": "tags", "q": "py"}))
        assert [r["label"] for r in json_of(ok)["results"]] == ["python"]

        Policy.view_tags = False
        denied = await send(
            app["kernel"], make_request("GET", url, query={"field": "tags", "q": "py"}, expects_json=True)
        )
        assert denied.status == 403

        Policy.view_tags = True
        Policy.create = False
        Policy.update = False
        denied = await send(
            app["kernel"], make_request("GET", url, query={"field": "tags", "q": "py"}, expects_json=True)
        )
        assert denied.status == 403


# ---------------------------------------------------------------------------
# ADM-06 — static segments are not shadowed by {id}
# ---------------------------------------------------------------------------


class TestRouteOrdering:
    def test_search_relation_route_is_not_shadowed(self, app):
        from hunt.admin.controllers import relation_search

        route, params = app["router"].dispatch("GET", f"{P}/resources/articles/search-relation")
        assert route.action is relation_search.search_relation
        assert params == {"resource_key": "articles"}

    @pytest.mark.parametrize("slug", ["delete", "field-toggle", "edit", "export_csv"])
    def test_action_routes_are_not_shadowed(self, app, slug):
        from hunt.admin.controllers import action

        route, params = app["router"].dispatch("POST", f"{P}/resources/articles/actions/{slug}")
        assert route.action is action.run
        assert params == {"resource_key": "articles", "action_slug": slug}

    def test_record_routes_still_resolve(self, app):
        from hunt.admin.controllers import resource

        assert app["router"].dispatch("GET", f"{P}/resources/articles/5")[0].action is resource.show
        assert app["router"].dispatch("GET", f"{P}/resources/articles/create")[0].action is resource.create
        assert app["router"].dispatch("POST", f"{P}/resources/articles/5/delete")[0].action is resource.destroy
        assert app["router"].dispatch("PUT", f"{P}/resources/articles/5")[0].action is resource.update
        assert app["router"].dispatch("DELETE", f"{P}/resources/articles/5")[0].action is resource.destroy

    async def test_searchable_belongs_to_returns_results_over_http(self, app):
        resp = await send(
            app["kernel"],
            make_request("GET", f"{P}/resources/articles/search-relation", query={"field": "author_id", "q": "Gra"}),
        )
        assert resp.status == 200
        assert json_of(resp)["results"] == [{"id": 2, "label": "Grace"}]


# ---------------------------------------------------------------------------
# ADM-08 — async gate and policy results never fail open
# ---------------------------------------------------------------------------


class TestAsyncGateAndPolicies:
    async def test_async_gate_returning_false_denies(self, app):
        async def gate(request):
            await asyncio.sleep(0)
            return False

        app["admin"].gate(gate)
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/articles", expects_json=True))
        assert resp.status == 403

    async def test_async_gate_returning_true_allows(self, app):
        async def gate(request):
            await asyncio.sleep(0)
            return True

        app["admin"].gate(gate)
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/articles"))
        assert resp.status == 200

    async def test_gate_that_raises_denies(self, app):
        def gate(request):
            raise RuntimeError("boom")

        app["admin"].gate(gate)
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/articles", expects_json=True))
        assert resp.status == 403

    def test_allow_accepts_only_true(self):
        from hunt.admin.authorization import _allow

        async def coro():
            return True

        assert _allow(True) is True
        for value in (False, None, 1, "yes", object(), [True]):
            assert _allow(value) is False
        assert _allow(coro()) is False  # an un-awaited coroutine is truthy but is not a decision

    async def test_async_policy_is_a_denial(self, app, monkeypatch):
        async def can_delete(self, request, instance=None):
            return True

        monkeypatch.setattr(ArticleResource, "can_delete", can_delete)
        resp = await send(app["kernel"], post(f"{P}/resources/articles/1/delete", expects_json=True))
        assert resp.status == 403
        assert article_ids() == [1, 2, 3]

    async def test_policy_that_raises_is_a_denial(self, app, monkeypatch):
        def can_view_any(self, request):
            raise RuntimeError("boom")

        monkeypatch.setattr(ArticleResource, "can_view_any", can_view_any)
        resp = await send(app["kernel"], make_request("GET", f"{P}/resources/articles", expects_json=True))
        assert resp.status == 403


# ---------------------------------------------------------------------------
# ADM-09 — system tools have their own authorization
# ---------------------------------------------------------------------------


class TestToolAuthorization:
    @pytest.mark.parametrize("tool", ["sessions", "cache", "logs", "queue"])
    async def test_sensitive_tools_are_denied_by_default(self, app, tool):
        resp = await send(app["kernel"], make_request("GET", f"{P}/{tool}", expects_json=True))
        assert resp.status == 403

    @pytest.mark.parametrize(
        ("path", "form"),
        [
            ("/queue/flush", {}),
            ("/queue/failed/1/retry", {}),
            ("/queue/failed/1/delete", {}),
            ("/cache/flush", {}),
            ("/cache/delete", {"key": "k"}),
            ("/sessions/user/1/revoke", {}),
            ("/sessions/abc/revoke", {}),
        ],
    )
    async def test_sensitive_tool_mutations_are_denied_by_default(self, app, path, form):
        resp = await send(app["kernel"], post(f"{P}{path}", form, expects_json=True))
        assert resp.status == 403

    async def test_tool_enabled_with_authorize_tool(self, app):
        app["admin"].authorize_tool("cache")
        resp = await send(app["kernel"], make_request("GET", f"{P}/cache"))
        assert resp.status == 200

    async def test_per_tool_callable_decides(self, app):
        app["admin"].authorize_tool("cache", lambda request: request.query("ok") == "1")
        assert (await send(app["kernel"], make_request("GET", f"{P}/cache", query={"ok": "1"}))).status == 200
        assert (await send(app["kernel"], make_request("GET", f"{P}/cache", expects_json=True))).status == 403

    async def test_tools_gate_hook_and_default_allowed_tools(self, app):
        assert (await send(app["kernel"], make_request("GET", f"{P}/routes"))).status == 200
        app["admin"].tools_gate(lambda request, tool: tool == "logs")
        assert (await send(app["kernel"], make_request("GET", f"{P}/routes", expects_json=True))).status == 403
        assert app["admin"].tool_allowed(make_request(), "logs") is True

    def test_truthy_non_bool_rule_result_is_a_denial(self, app):
        app["admin"].authorize_tool("cache", lambda request: "yes")
        assert app["admin"].tool_allowed(make_request(), "cache") is False

    def test_nav_only_lists_permitted_tools(self, app):
        def labels(request):
            groups = app["admin"]._build_nav(request)
            system = [g for g in groups if g.label == "System"]
            return [item.label for item in system[0].items] if system else []

        request = make_request()
        assert "Queue" not in labels(request)
        assert "Health" in labels(request)
        app["admin"].authorize_tool("queue")
        assert "Queue" in labels(request)

    async def test_custom_tool_route_is_registered(self, app):
        class Reports:
            def index(self, request):
                return "report page"

        app["admin"].tool("Sales Reports", Reports)
        nav = app["admin"]._build_nav(make_request())
        tools = next(g for g in nav if g.label == "Tools")
        assert tools.items[0].url == f"{P}/tools/sales-reports"

        resp = await send(app["kernel"], make_request("GET", f"{P}/tools/sales-reports"))
        assert resp.status == 200
        assert body_of(resp) == "report page"

        missing = await send(app["kernel"], make_request("GET", f"{P}/tools/nope", expects_json=True))
        assert missing.status == 404

        app["admin"].authorize_tool("sales-reports", False)
        denied = await send(app["kernel"], make_request("GET", f"{P}/tools/sales-reports", expects_json=True))
        assert denied.status == 403


# ---------------------------------------------------------------------------
# ADM-10 — admin routes are CSRF protected without global middleware
# ---------------------------------------------------------------------------


class TestAdminCsrf:
    async def test_post_without_token_is_rejected_without_global_csrf(self, app):
        request = make_request("POST", f"{P}/resources/articles/1/delete", form={}, expects_json=True)
        resp = await send(app["kernel"], request)
        assert resp.status == 419
        assert article_ids() == [1, 2, 3]

    async def test_post_with_wrong_token_is_rejected(self, app):
        request = make_request("POST", f"{P}/resources/articles/1/delete", form={"_token": "nope"}, expects_json=True)
        assert (await send(app["kernel"], request)).status == 419
        assert article_ids() == [1, 2, 3]

    async def test_post_with_token_passes(self, app):
        resp = await send(app["kernel"], post(f"{P}/resources/articles/1/delete"))
        assert resp.status == 302
        assert article_ids() == [2, 3]

    async def test_header_token_is_accepted(self, app):
        request = make_request(
            "POST",
            f"{P}/resources/articles/1/delete",
            json_body={},
            headers={"X-CSRF-TOKEN": CSRF},
        )
        assert (await send(app["kernel"], request)).status == 302

    async def test_post_without_session_is_rejected(self, app):
        kernel = make_kernel(app["router"], session=None)
        request = make_request("POST", f"{P}/resources/articles/1/delete", form={"_token": CSRF}, expects_json=True)
        assert (await send(kernel, request)).status == 419

    async def test_works_alongside_global_csrf_middleware(self, app):
        from hunt.http.middleware.csrf import VerifyCsrfToken

        kernel = make_kernel(app["router"], app["session"], extra_global=[VerifyCsrfToken])
        assert (await send(kernel, post(f"{P}/resources/articles/1/delete"))).status == 302
        bad = make_request("POST", f"{P}/resources/articles/2/delete", form={}, expects_json=True)
        assert (await send(kernel, bad)).status == 419
        assert article_ids() == [2, 3]

    def test_every_admin_route_carries_the_gate_and_csrf(self, app):
        from hunt.admin.middleware.csrf import AdminVerifyCsrfToken
        from hunt.admin.middleware.gate import AdminGate

        for route in app["router"].routes():
            if "/assets/" in route.uri:
                continue
            assert AdminGate in route._middleware, route.uri
            assert AdminVerifyCsrfToken in route._middleware, route.uri

    async def test_exempt_urls_do_not_apply_to_admin(self, app, monkeypatch):
        from hunt.http.middleware.csrf import VerifyCsrfToken

        monkeypatch.setattr(VerifyCsrfToken, "except_urls", [P])
        request = make_request("POST", f"{P}/resources/articles/1/delete", form={}, expects_json=True)
        assert (await send(app["kernel"], request)).status == 419


def test_direct_policy_denial_raises_403(app):
    from hunt.admin.controllers import resource

    Policy.view = False
    with pytest.raises(HttpException) as exc:
        resource.index(make_request("GET", f"{P}/resources/articles"), "articles")
    assert exc.value.status == 403
