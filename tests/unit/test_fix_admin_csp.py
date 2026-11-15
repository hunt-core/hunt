"""The admin panel runs under ``script-src 'self'`` (ADM-15, long-term part).

Every admin page is rendered through the real admin application and checked
for: a Content-Security-Policy without ``'unsafe-inline'`` / ``'unsafe-eval'``
for scripts, no inline ``<script>`` (JSON data blocks excepted), no inline
event-handler attributes, no ``javascript:`` URLs, and ``<script src>`` tags
that all point at files the admin asset route really serves.

The behaviour that used to live in inline scripts is in
``src/hunt/admin/static/admin.js`` and ``admin-media.js``; the markup hooks
they rely on are asserted here and their logic is exercised under Node by
``tests/unit/js/admin.test.js`` (run from this module).
"""

# ruff: noqa: RUF012  (test models declare fillable as plain class attributes)
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from hunt.admin import Action, AdminResource, SelectFilter
from hunt.admin.fields import BelongsTo, BelongsToMany, Boolean, Markdown, MediaBrowser, RichText, Text
from hunt.database.model import Model
from hunt.http.router import Router
from tests.unit.test_fix_admin_support import (  # noqa: F401  (fixtures)
    CSRF,
    FakeSession,
    admin,
    admin_db,
    body_of,
    make_kernel,
    make_request,
    run_sql,
    send,
)

P = "/hunt-admin"
REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "src" / "hunt" / "admin" / "static"
TEMPLATES = REPO / "src" / "hunt" / "admin" / "templates" / "admin"
JS_TESTS = Path(__file__).resolve().parent / "js" / "admin.test.js"
FIRST_PARTY_JS = ("admin.js", "admin-media.js")
NODE = shutil.which("node")

URL_ATTRIBUTES = ("href", "src", "action", "formaction", "xlink:href", "data", "poster")


# ---------------------------------------------------------------------------
# HTML inspection
# ---------------------------------------------------------------------------


class _Page(HTMLParser):
    """Collects what a Content-Security-Policy would care about in a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.event_handlers: list[str] = []
        self.javascript_urls: list[str] = []
        self.inline_scripts: list[str] = []
        self.script_sources: list[str] = []
        self.json_blocks: dict[str, str] = {}
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self._script: dict[str, str | None] | None = None
        self._script_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        for name, value in attrs:
            if name.lower().startswith("on"):
                self.event_handlers.append(f"<{tag} {name}={value!r}>")
            if name.lower() in URL_ATTRIBUTES and re.match(r"\s*(javascript|vbscript)\s*:", value or "", re.I):
                self.javascript_urls.append(f"<{tag} {name}={value!r}>")
        if tag == "script":
            self._script = attributes
            self._script_text = []

    handle_startendtag = handle_starttag

    def handle_data(self, data):
        if self._script is not None:
            self._script_text.append(data)

    def handle_endtag(self, tag):
        if tag != "script" or self._script is None:
            return
        attributes, text = self._script, "".join(self._script_text)
        self._script = None
        if attributes.get("src"):
            self.script_sources.append(attributes["src"] or "")
            assert text.strip() == "", "a <script src> must not also carry inline code"
        elif (attributes.get("type") or "").lower() == "application/json":
            self.json_blocks[attributes.get("id") or ""] = text
        else:
            self.inline_scripts.append(text.strip()[:120])

    def find(self, tag: str | None = None, **wanted: str | None) -> list[dict[str, str | None]]:
        """Elements matching ``tag`` and attributes (``None`` = attribute merely present)."""
        found = []
        for name, attributes in self.elements:
            if tag is not None and name != tag:
                continue
            if all(
                key.replace("_", "-") in attributes and (value is None or attributes[key.replace("_", "-")] == value)
                for key, value in wanted.items()
            ):
                found.append(attributes)
        return found


def parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    page.close()
    return page


def directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0].lower()] = tokens[1:]
    return out


def assert_strict(response, *, prefix: str = P) -> _Page:
    """Assert one admin response is usable under, and sends, the strict policy."""
    csp = response.get_header("Content-Security-Policy")
    assert csp, "admin page sent no Content-Security-Policy"
    policy = directives(csp)
    assert policy["script-src"] == ["'self'"]
    for name in ("script-src", "script-src-elem", "script-src-attr", "default-src"):
        assert "'unsafe-inline'" not in policy.get(name, [])
        assert "'unsafe-eval'" not in policy.get(name, [])
    assert not any(token.startswith("'nonce-") for token in policy["script-src"])

    page = parse(body_of(response))
    assert page.event_handlers == []
    assert page.javascript_urls == []
    assert page.inline_scripts == []
    assert page.script_sources, "the layout must load admin.js"
    for src in page.script_sources:
        assert src.startswith(f"{prefix}/assets/"), src
        name = src[len(f"{prefix}/assets/") :]
        assert re.fullmatch(r"[\w.-]+\.js", name), src
        assert (STATIC / name).is_file(), f"{src} is not a bundled asset"
    assert f"{prefix}/assets/admin.js" in page.script_sources
    return page


# ---------------------------------------------------------------------------
# A small application that reaches every template branch with script hooks
# ---------------------------------------------------------------------------


class Writer(Model):
    table = "fc_writers"
    fillable = ["name"]
    timestamps = False


class Topic(Model):
    table = "fc_topics"
    fillable = ["name"]
    timestamps = False


class Story(Model):
    table = "fc_stories"
    fillable = ["title", "summary", "body", "status", "published", "writer_id"]
    timestamps = False

    def topics(self):
        return self.belongs_to_many(Topic, "fc_story_topic", "story_id", "topic_id")


class WriterResource(AdminResource):
    model = Writer
    search_columns = ["name"]

    def fields(self):
        return [Text("Name", attribute="name")]

    def can_view_any(self, request):
        return True


class TopicResource(AdminResource):
    model = Topic
    search_columns = ["name"]

    def fields(self):
        return [Text("Name", attribute="name")]

    def can_view_any(self, request):
        return True


class StatusFilter(SelectFilter):
    name = "Status"
    attribute = "status"

    def options(self):
        return [("draft", "Draft"), ("published", "Published")]


class PurgeAction(Action):
    name = "Purge"
    destructive = True
    confirmation_text = "Purge Bob's stories? It can't be undone."

    def handle(self, request, models):
        from hunt.admin import ActionResponse

        return ActionResponse.success("ok")


class FeatureAction(Action):
    name = "Feature"

    def handle(self, request, models):
        from hunt.admin import ActionResponse

        return ActionResponse.success("ok")


class StoryResource(AdminResource):
    model = Story
    search_columns = ["title"]
    per_page = 2

    def fields(self):
        return [
            Text("Title", attribute="title").sortable(),
            Boolean("Published", attribute="published"),
            RichText("Summary", attribute="summary"),
            Markdown("Body", attribute="body"),
            BelongsTo("Writer", WriterResource, attribute="writer_id").searchable(),
            BelongsToMany("Topics", TopicResource, "topics"),
            MediaBrowser("Media"),
        ]

    def filters(self):
        return [StatusFilter()]

    def actions(self):
        return [PurgeAction(), FeatureAction()]

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
    from hunt.cache.manager import Cache
    from hunt.session.registry import SessionRegistry

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(admin, "csp_allow_inline_scripts", False)
    run_sql("CREATE TABLE fc_writers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    run_sql("CREATE TABLE fc_topics (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    run_sql("CREATE TABLE fc_story_topic (story_id INTEGER, topic_id INTEGER)")
    run_sql(
        "CREATE TABLE fc_stories (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, summary TEXT, body TEXT,"
        " status TEXT, published INTEGER DEFAULT 0, writer_id INTEGER)"
    )
    run_sql("INSERT INTO fc_writers (name) VALUES ('Ada'), ('Grace')")
    run_sql("INSERT INTO fc_topics (name) VALUES ('python'), ('asgi')")
    run_sql(
        "INSERT INTO fc_stories (title, summary, body, status, published, writer_id) VALUES"
        " ('one', '<p>hi</p>', '# one <img src=x onerror=alert(1)>', 'draft', 1, 1),"
        " ('two', '', 'two', 'draft', 0, 2),"
        " ('three', '', 'three', 'published', 0, 1)"
    )
    run_sql("INSERT INTO fc_story_topic (story_id, topic_id) VALUES (1, 1), (1, 2)")

    # Queue monitor: one failed job, so the detail dialog and its buttons render.
    run_sql(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, queue VARCHAR(255) DEFAULT 'default',"
        " payload TEXT, attempts SMALLINT DEFAULT 0, reserved_at INTEGER, available_at INTEGER, created_at INTEGER)"
    )
    run_sql(
        "CREATE TABLE jobs_failed (id INTEGER PRIMARY KEY AUTOINCREMENT, uuid VARCHAR(36) UNIQUE,"
        " connection VARCHAR(255), queue VARCHAR(255), payload TEXT, exception TEXT, failed_at INTEGER)"
    )
    run_sql(
        "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at) VALUES"
        " ('u-1', 'database', 'default', '{\"job\": \"app.jobs.Send\"}', 'Boom: it''s broken', 1731664800)"
    )

    # Sessions manager: one registered session.
    run_sql(
        "CREATE TABLE user_sessions (id VARCHAR(128) PRIMARY KEY, user_id VARCHAR(64), guard VARCHAR(32),"
        " ip_address VARCHAR(64), user_agent TEXT, last_active_at INTEGER)"
    )
    SessionRegistry().register("raw-session-id-0123456789abcdef", 7, "web", "10.0.0.1", "pytest-agent")

    # Log viewer: an entry with an exception (the expandable row).
    log_file = tmp_path / "hunt.log"
    log_file.write_text(
        json.dumps({"ts": "2026-11-15 10:00:00", "level": "error", "message": "failed", "exception": "Traceback x"})
        + "\n"
        + json.dumps({"ts": "2026-11-15 10:00:01", "level": "info", "message": "fine"})
        + "\n"
    )
    monkeypatch.setenv("LOG_PATH", str(log_file))

    # Cache inspector: one entry (the per-key delete form).
    saved_cache = (Cache._driver, Cache._store) if hasattr(Cache, "_driver") else None
    Cache.configure("array")
    Cache._get_store().put("csp:key", "value", 600)

    admin.resource(StoryResource)
    admin.resource(WriterResource)
    admin.resource(TopicResource)
    admin.gate(lambda request: True)
    for tool in ("sessions", "cache", "logs", "queue"):
        admin.authorize_tool(tool)
    router = Router()
    admin.register_to(router)
    session = FakeSession()
    try:
        yield {"admin": admin, "router": router, "session": session, "kernel": make_kernel(router, session)}
    finally:
        Cache._get_store().flush()
        Cache.configure("array")
        if saved_cache is not None:
            Cache._driver, Cache._store = saved_cache


async def get(app, path: str, **query: str):
    response = await send(app["kernel"], make_request("GET", f"{P}{path}", query=query or None))
    assert response.status == 200, f"{path}: {response.status}"
    return response


ALL_PAGES = [
    "/",
    "/resources/stories",
    "/resources/stories/1",
    "/resources/stories/create",
    "/resources/stories/1/edit",
    "/resources/writers",
    "/resources/writers/1",
    "/media",
    "/health",
    "/schedule",
    "/routes",
    "/queue",
    "/cache",
    "/logs",
    "/sessions",
]


# ---------------------------------------------------------------------------
# The policy and the pages
# ---------------------------------------------------------------------------


class TestStrictScriptPolicy:
    def test_policy_string(self, admin):  # noqa: F811
        assert admin.csp_allow_inline_scripts is False
        assert admin.content_security_policy() == (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "img-src * data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'"
        )

    @pytest.mark.parametrize("path", ALL_PAGES)
    async def test_every_page_is_free_of_inline_script(self, app, path):
        assert_strict(await get(app, path))

    async def test_pages_with_query_strings(self, app):
        assert_strict(await get(app, "/resources/stories", search="o", sort="title", dir="desc", page="2"))
        assert_strict(await get(app, "/resources/stories", filter_status="draft"))
        assert_strict(await get(app, "/logs", level="error", search="failed"))

    async def test_validation_error_form_is_strict_too(self, app):
        app["session"].all_flash = lambda: {
            "_errors": {"title": ["The title field is required."]},
            "_old_input": {"title": "", "body": "</script><script>alert(1)</script>"},
            "admin_error": "Could not save.",
            "admin_success": "Saved.",
        }
        page = assert_strict(await get(app, "/resources/stories/create"))
        assert page.find("textarea", data_hunt_markdown=None)

    async def test_custom_prefix(self, app):
        app["admin"].prefix = "/backoffice"
        router = Router()
        app["admin"].register_to(router)
        kernel = make_kernel(router, app["session"])
        response = await send(kernel, make_request("GET", "/backoffice/resources/stories/create"))
        assert response.status == 200
        page = assert_strict(response, prefix="/backoffice")
        assert json.loads(page.json_blocks["hunt-admin-config"])["prefix"] == "/backoffice"

    async def test_template_sources_have_no_inline_script(self):
        """Covers template branches the fixtures above do not reach."""
        handler = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.I)
        offenders = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            source = path.read_text(encoding="utf-8")
            for number, line in enumerate(source.splitlines(), 1):
                if handler.search(line) or "javascript:" in line.lower():
                    offenders.append(f"{path.relative_to(TEMPLATES)}:{number}: {line.strip()[:80]}")
            for match in re.finditer(r"<script\b([^>]*)>", source, re.I):
                attributes = match.group(1)
                if "src=" not in attributes and 'type="application/json"' not in attributes:
                    offenders.append(f"{path.relative_to(TEMPLATES)}: inline <script{attributes}>")
        assert offenders == []

    def test_no_admin_python_builds_inline_script(self):
        """Fields and controllers must not emit script from Python strings either (docstrings excepted)."""
        pattern = re.compile(r"""<script|\son[a-z]+\s*=\s*\\?["']|javascript:""", re.I)
        offenders = []
        for path in sorted((REPO / "src" / "hunt" / "admin").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    first = node.body[0] if node.body else None
                    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                        docstrings.add(id(first.value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    if pattern.search(node.value):
                        offenders.append(f"{path.name}:{node.lineno}: {node.value.strip()[:80]}")
        assert offenders == []


class TestConfigBlock:
    async def test_config_is_json_data_not_script(self, app):
        page = assert_strict(await get(app, "/resources/stories"))
        config = json.loads(page.json_blocks["hunt-admin-config"])
        assert config == {"prefix": P, "resourceKey": "stories", "csrfToken": CSRF}

    async def test_config_cannot_break_out_of_its_block(self, app):
        app["session"].data["_csrf_token"] = "</script><script>alert(1)</script>"
        response = await get(app, "/")
        html = body_of(response)
        assert "</script><script>alert(1)" not in html
        page = assert_strict(response)
        assert json.loads(page.json_blocks["hunt-admin-config"])["csrfToken"] == "</script><script>alert(1)</script>"

    async def test_config_precedes_admin_js_in_the_head(self, app):
        html = body_of(await get(app, "/"))
        head = html.split("</head>", 1)[0]
        assert head.index('id="hunt-admin-config"') < head.index(f'src="{P}/assets/admin.js"')


# ---------------------------------------------------------------------------
# Markup hooks each behaviour in admin.js relies on
# ---------------------------------------------------------------------------


class TestBehaviourHooks:
    async def test_layout(self, app):
        page = assert_strict(await get(app, "/"))
        assert len(page.find(data_hunt_action="sidebar-open")) == 1
        assert len(page.find(data_hunt_action="sidebar-close")) == 2  # backdrop and close button
        assert len(page.find(data_hunt_action="media-close")) == 2
        groups = page.find("button", data_hunt_action="nav-group-toggle")
        assert groups and all(g.get("data-group") for g in groups)
        assert page.find("input", id="global-search") and page.find("div", id="search-results")
        for element_id in ("_hunt-media-modal", "_hunt-media-grid", "_hunt-media-status", "_hunt-media-upload"):
            assert page.find(id=element_id), element_id
        assert page.find(id="mobile-sidebar")

    async def test_resource_index(self, app):
        page = assert_strict(await get(app, "/resources/stories"))
        # confirmation text is attribute data, including the apostrophes
        confirms = [f.get("data-confirm") for f in page.find("form", data_confirm=None)]
        assert confirms.count("Purge Bob's stories? It can't be undone.") == 2  # bulk bar + modal
        assert confirms.count("Delete this record?") == 2  # one per row on this page
        # bulk actions: every action form is marked and has an ids input to fill
        bulk = page.find("form", data_hunt_bulk=None)
        assert len(bulk) == 4  # 2 actions x (bulk bar + modal)
        assert len(page.find("input", name="ids")) == 4
        assert all(f.get("action", "").startswith(f"{P}/resources/stories/actions/") for f in bulk)
        not_destructive = [f for f in bulk if f.get("action", "").endswith("/feature")]
        assert not_destructive and all("data-confirm" not in f for f in not_destructive)
        assert len(page.find("input", data_hunt_change="select-all")) == 1
        assert len(page.find("input", **{"class": None, "type": "checkbox"})) >= 3
        assert len(page.find(data_hunt_action="actions-open")) == 1
        assert len(page.find(data_hunt_action="actions-close")) == 2
        assert page.find(id="actions-modal")
        # boolean toggle
        toggles = page.find("button", data_hunt_action="toggle-field")
        assert len(toggles) == 2
        assert all(re.fullmatch(rf"{P}/resources/stories/\d+/field-toggle", t["data-toggle-url"]) for t in toggles)
        assert all(t["data-attribute"] == "published" for t in toggles)
        # filters, per-page selector, sort links, pagination
        assert len(page.find("select", data_hunt_change="submit", name="filter_status")) == 1
        assert len(page.find("select", data_hunt_change="per-page")) == 1
        hrefs = [a.get("href") or "" for a in page.find("a")]
        assert any("sort=title" in h and "dir=" in h for h in hrefs)
        assert any("page=2" in h for h in hrefs)

    async def test_resource_form(self, app):
        page = assert_strict(await get(app, "/resources/stories/1/edit"))
        # Trix: loaded from the asset route, bound to its hidden input
        assert f"{P}/assets/trix.umd.min.js" in page.script_sources
        assert page.find("trix-editor", input="field_summary")
        assert page.find("input", id="field_summary", type="hidden")
        # EasyMDE: admin.js finds the textarea by attribute and loads the editor
        markdown = page.find("textarea", data_hunt_markdown=None)
        assert [m.get("id") for m in markdown] == ["field_body"]
        assert "data-hunt-readonly" not in markdown[0]
        # searchable BelongsTo
        wrap = page.find("div", data_hunt_autocomplete="writer_id")
        assert len(wrap) == 1
        assert page.find("input", data_hunt_role="value", name="writer_id", type="hidden")
        assert page.find("input", data_hunt_role="search", id="field_writer_id_display")
        assert page.find("ul", data_hunt_role="results", id="field_writer_id_results")
        # BelongsToMany
        assert len(page.find("div", data_hunt_btm="topics")) == 1
        hidden = page.find("input", data_hunt_role="value", name="topics")
        assert hidden and hidden[0]["value"] == "1,2"
        assert page.find("div", data_hunt_role="chips")
        assert len(page.find("button", **{"class": "btm-remove ml-0.5 leading-none"})) == 2
        assert page.find("input", data_hunt_role="search", id="btm_search_topics")
        assert page.find("ul", data_hunt_role="results", id="btm_results_topics")
        # media browser field
        pick = page.find("button", data_hunt_action="media-pick")
        copy = page.find("button", data_hunt_action="copy-value")
        assert pick[0]["data-hunt-target"] == copy[0]["data-hunt-target"] == "_media_url_media"
        assert page.find("input", id="_media_url_media")

    async def test_readonly_fields_are_not_wired_for_editing(self, app, monkeypatch):
        original = StoryResource.fields

        def readonly_fields(self):
            return [f.readonly() if f.attribute in ("body", "topics") else f for f in original(self)]

        monkeypatch.setattr(StoryResource, "fields", readonly_fields)
        page = assert_strict(await get(app, "/resources/stories/1/edit"))
        assert page.find("textarea", data_hunt_markdown=None, data_hunt_readonly=None)
        assert page.find("div", data_hunt_btm=None) == []

    async def test_show_page_delete_asks_first(self, app):
        page = assert_strict(await get(app, "/resources/stories/1"))
        forms = page.find("form", data_confirm="Are you sure you want to delete this record?")
        assert len(forms) == 1 and forms[0]["action"] == f"{P}/resources/stories/1/delete"

    async def test_media_page(self, app):
        page = assert_strict(await get(app, "/media"))
        assert page.script_sources[-1] == f"{P}/assets/admin-media.js"
        assert page.script_sources.index(f"{P}/assets/admin.js") < page.script_sources.index(
            f"{P}/assets/admin-media.js"
        )
        for element_id in (
            "media-grid",
            "media-empty",
            "media-loading",
            "media-file-input",
            "upload-progress",
            "upload-progress-text",
            "media-flash",
            "copy-toast",
        ):
            assert page.find(id=element_id), element_id

    async def test_tool_pages(self, app):
        queue = assert_strict(await get(app, "/queue"))
        view = queue.find("button", data_hunt_action="dialog-open")
        assert len(view) == 1 and queue.find("dialog", id=view[0]["data-hunt-target"])
        assert queue.find("form", data_confirm="Flush all failed jobs? This cannot be undone.")
        assert queue.find("form", data_confirm="Delete this failed job?")

        cache = assert_strict(await get(app, "/cache"))
        assert cache.find("form", data_confirm="Flush the entire cache?", action=f"{P}/cache/flush")
        assert cache.find("form", data_confirm="Delete this cache key?", action=f"{P}/cache/delete")

        logs = assert_strict(await get(app, "/logs"))
        assert len(logs.find("tr", data_hunt_action="toggle-exception")) == 1  # only the row with an exception

        sessions = assert_strict(await get(app, "/sessions"))
        assert sessions.find("form", data_confirm="Revoke this session?")


# ---------------------------------------------------------------------------
# The script files themselves
# ---------------------------------------------------------------------------


class TestFirstPartyScripts:
    async def test_asset_route_serves_them_as_javascript(self, app):
        for name in FIRST_PARTY_JS:
            response = await send(app["kernel"], make_request("GET", f"{P}/assets/{name}"))
            assert response.status == 200
            assert "javascript" in response.content_type
            assert response._body == (STATIC / name).read_bytes()

    def test_they_contain_no_template_syntax_or_per_user_data(self):
        """The asset route is public: the files are static and identical for everyone."""
        for name in FIRST_PARTY_JS:
            source = (STATIC / name).read_text(encoding="utf-8")
            assert "{{" not in source and "{%" not in source
            assert CSRF not in source
            # no embedded token-like literal (keys, hashes, signed values)
            assert not re.search(r"""["'][A-Za-z0-9+=_]{32,}["']""", source), name

    def test_admin_js_lazy_loads_only_bundled_files(self):
        source = (STATIC / "admin.js").read_text(encoding="utf-8")
        assets = set(re.findall(r"'/assets/([\w.-]+)'", source))
        assert assets == {"easymde.min.js", "easymde.min.css", "font-awesome.min.css"}
        for name in assets:
            assert (STATIC / name).is_file()
        assert not re.search(r"https?://", source)

    def test_markdown_preview_stays_disabled(self):
        source = (STATIC / "admin.js").read_text(encoding="utf-8")
        toolbar = source.split("toolbar: [", 1)[1].split("spellChecker", 1)[0]
        assert "'preview'" not in toolbar and "'side-by-side'" not in toolbar
        assert "togglePreview: null" in toolbar and "toggleSideBySide: null" in toolbar
        assert "sanitizerFunction" in toolbar and "previewRender" in toolbar

    @pytest.mark.parametrize("name", ["trix.umd.min.js", "easymde.min.js"])
    def test_vendored_editors_do_not_need_eval(self, name):
        source = (STATIC / name).read_text(encoding="utf-8")
        assert not re.search(r"\beval\s*\(", source)
        assert not re.search(r"\bFunction\s*\(\s*[\"'`]", source)
        assert not re.search(r"new\s+Function\b", source)
        assert not re.search(r"set(?:Timeout|Interval)\(\s*[\"'`]", source)
        assert not re.search(r"createElement\(\s*[\"']script", source)
        assert "document.write" not in source

    @pytest.mark.skipif(NODE is None, reason="node is not installed")
    @pytest.mark.parametrize("name", FIRST_PARTY_JS)
    def test_syntax(self, name):
        result = subprocess.run([NODE, "--check", str(STATIC / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr

    @pytest.mark.skipif(NODE is None, reason="node is not installed")
    def test_behaviour_under_node(self):
        result = subprocess.run([NODE, "--test", str(JS_TESTS)], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


# ---------------------------------------------------------------------------
# Opt-out for applications that still have old published templates
# ---------------------------------------------------------------------------


class TestInlineScriptOptOut:
    async def test_default_is_strict(self, app):
        policy = directives((await get(app, "/")).get_header("Content-Security-Policy"))
        assert policy["script-src"] == ["'self'"]

    async def test_switch_restores_unsafe_inline_for_scripts_only(self, app, monkeypatch):
        strict = directives(app["admin"].content_security_policy())
        monkeypatch.setattr(app["admin"], "csp_allow_inline_scripts", True)
        for path in ("/", "/resources/stories", "/media"):
            policy = directives((await get(app, path)).get_header("Content-Security-Policy"))
            assert policy["script-src"] == ["'self'", "'unsafe-inline'"]
            assert "'unsafe-eval'" not in policy["script-src"]
            assert {k: v for k, v in policy.items() if k != "script-src"} == {
                k: v for k, v in strict.items() if k != "script-src"
            }

    @pytest.mark.parametrize("value", [None, 0, "", "yes", 1])
    def test_only_true_relaxes_the_policy(self, admin, monkeypatch, value):  # noqa: F811
        monkeypatch.setattr(admin, "csp_allow_inline_scripts", value)
        assert "'unsafe-inline'" not in directives(admin.content_security_policy())["script-src"]

    async def test_published_template_with_inline_script(self, app, tmp_path, monkeypatch):
        """An overridden template is served as-is; the header decides whether its script runs."""
        from hunt.admin import application

        views = tmp_path / "resources" / "views" / "admin"
        views.mkdir(parents=True)
        (views / "dashboard.html").write_text(
            '{% extends "admin/layout.html" %}{% block content %}<script>legacy()</script>{% endblock %}'
        )
        application._get_env.cache_clear()
        try:
            response = await get(app, "/")
            assert "<script>legacy()</script>" in body_of(response)
            # strict by default: the browser will refuse the legacy block
            assert directives(response.get_header("Content-Security-Policy"))["script-src"] == ["'self'"]
            monkeypatch.setattr(app["admin"], "csp_allow_inline_scripts", True)
            response = await get(app, "/")
            assert "'unsafe-inline'" in directives(response.get_header("Content-Security-Policy"))["script-src"]
        finally:
            application._get_env.cache_clear()
