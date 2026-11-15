"""Regression tests for the admin system tools, templates and vendored assets.

ADM-14 (asset integrity), ADM-15 (Markdown preview) and the low-severity
items ADM-L1 to ADM-L6, ADM-L12, ADM-L16 and ADM-L17.
"""

# ruff: noqa: RUF012  (test models declare fillable/hidden as plain class attributes)
from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hunt.admin import Action, AdminResource
from hunt.admin.fields import Markdown, RichText, Text
from hunt.database.model import Model
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
REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "src" / "hunt" / "admin" / "static"
TEMPLATES = REPO / "src" / "hunt" / "admin" / "templates" / "admin"


class Note(Model):
    table = "ft_notes"
    fillable = ["title", "body"]
    timestamps = False


class ArchiveAction(Action):
    name = "Archive"
    destructive = True
    confirmation_text = "Archive Bob's notes? It can't be undone."

    def handle(self, request, models):
        from hunt.admin import ActionResponse

        return ActionResponse.success("ok")


class NoteResource(AdminResource):
    model = Note

    def fields(self):
        return [Text("Title", attribute="title"), Markdown("Body", attribute="body")]

    def actions(self):
        return [ArchiveAction()]

    def can_view_any(self, request):
        return True

    def can_create(self, request):
        return True

    def can_update(self, request, instance=None):
        return True


@pytest.fixture
def app(admin, admin_db, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    run_sql("CREATE TABLE ft_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT)")
    run_sql("INSERT INTO ft_notes (title, body) VALUES ('first', '<img src=x onerror=alert(1)>')")
    admin.resource(NoteResource)
    admin.gate(lambda request: True)
    for tool in ("sessions", "cache", "logs", "queue"):
        admin.authorize_tool(tool)
    router = Router()
    admin.register_to(router)
    session = FakeSession()
    return {"admin": admin, "router": router, "session": session, "kernel": make_kernel(router, session)}


def post(path, form=None, **kw):
    return make_request("POST", path, form={"_token": CSRF, **(form or {})}, **kw)


# ---------------------------------------------------------------------------
# ADM-14 — vendored assets are pinned by hash and patches must apply
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def build_script():
    path = REPO / "scripts" / "build_admin_assets.py"
    spec = importlib.util.spec_from_file_location("_hunt_build_admin_assets", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


class TestVendorAssetIntegrity:
    def test_committed_assets_match_the_pinned_hashes(self, build_script):
        """This is the lock-file check: CI fails when a vendored file drifts."""
        assert build_script.verify_committed() == []

    def test_every_vendored_file_is_pinned(self, build_script):
        vendored = {
            str(p.relative_to(STATIC)).replace("\\", "/")
            for p in STATIC.rglob("*")
            if p.is_file() and p.relative_to(STATIC).as_posix() not in build_script.FIRST_PARTY
        }
        assert build_script.FIRST_PARTY == {"admin.css", "admin.js", "admin-media.js"}
        assert vendored == set(build_script.OUTPUTS)

    def test_every_source_url_is_https_and_versioned(self, build_script):
        for url, digest in build_script.SOURCES.items():
            assert url.startswith("https://unpkg.com/")
            assert "@" in url
            assert len(digest) == 64 and int(digest, 16) >= 0

    def test_download_with_wrong_hash_aborts(self, build_script, monkeypatch):
        url = next(iter(build_script.SOURCES))

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"tampered"

        monkeypatch.setattr(build_script.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
        with pytest.raises(build_script.BuildError, match="SHA-256 mismatch"):
            build_script.fetch(url)

    def test_unpinned_url_is_refused(self, build_script, monkeypatch):
        called = []
        monkeypatch.setattr(build_script.urllib.request, "urlopen", lambda *a, **k: called.append(a))
        with pytest.raises(build_script.BuildError, match="not in SOURCES"):
            build_script.fetch("https://unpkg.com/trix@latest/dist/trix.umd.min.js")
        assert called == []

    def test_patch_that_does_not_apply_aborts(self, build_script):
        with pytest.raises(build_script.BuildError, match="expected 1 occurrence"):
            build_script.patch_easymde_js(b"upstream changed its minified output")
        with pytest.raises(build_script.BuildError, match="expected 6 occurrence"):
            build_script.patch_font_awesome_css(b"@font-face{src:url('fonts/x')}")
        with pytest.raises(build_script.BuildError, match="@import"):
            build_script.patch_easymde_css(b"@import url(https://example.com/other.css);")

    def test_build_writes_nothing_when_a_download_is_tampered(self, build_script, monkeypatch, tmp_path):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"tampered"

        monkeypatch.setattr(build_script.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
        monkeypatch.setattr(build_script, "STATIC", tmp_path / "static")
        assert build_script.main([]) == 1
        assert not (tmp_path / "static").exists()

    def test_verify_reports_a_modified_file(self, build_script, tmp_path):
        target = tmp_path / "static"
        (target / "fonts").mkdir(parents=True)
        for rel in build_script.OUTPUTS:
            (target / rel).write_bytes((STATIC / rel).read_bytes())
        assert build_script.verify_committed(target) == []
        (target / "trix.umd.min.js").write_bytes(b"alert(1)")
        problems = build_script.verify_committed(target)
        assert len(problems) == 1 and problems[0].startswith("trix.umd.min.js")

    def test_verify_reports_an_unpinned_file(self, build_script, tmp_path):
        target = tmp_path / "static"
        (target / "fonts").mkdir(parents=True)
        for rel in build_script.OUTPUTS:
            (target / rel).write_bytes((STATIC / rel).read_bytes())
        (target / "admin.js").write_text("// first-party, edited by hand")
        assert build_script.verify_committed(target) == []
        (target / "extra.min.js").write_bytes(b"alert(1)")
        problems = build_script.verify_committed(target)
        assert len(problems) == 1 and problems[0].startswith("extra.min.js")

    def test_trix_is_a_release_with_the_known_xss_fixes(self, build_script):
        version = tuple(int(part) for part in build_script.VERSIONS["trix"].split("."))
        assert version >= (2, 1, 18)
        head = (STATIC / "trix.umd.min.js").read_text(encoding="utf-8")[:200]
        assert f"Trix {build_script.VERSIONS['trix']}" in head

    def test_patched_assets_carry_their_patches(self):
        fa = (STATIC / "font-awesome.min.css").read_text(encoding="utf-8")
        assert "../fonts/" not in fa and "fonts/fontawesome-webfont" in fa
        mde = (STATIC / "easymde.min.js").read_text(encoding="utf-8")
        assert 'n[i].href.indexOf("font-awesome")>-1' in mde
        assert "@import" not in (STATIC / "easymde.min.css").read_text(encoding="utf-8")

    def test_rich_text_form_still_matches_trix(self):
        form = (TEMPLATES / "resource" / "_form.html").read_text(encoding="utf-8")
        layout = (TEMPLATES / "layout.html").read_text(encoding="utf-8")
        trix = (STATIC / "trix.umd.min.js").read_text(encoding="utf-8")
        assert '<trix-editor input="field_{{ field.attribute }}"' in form
        assert "/assets/trix.umd.min.js" in layout and "/assets/trix.css" in layout
        assert '"trix-editor"' in trix  # the custom element the form relies on
        assert ").Trix=" in trix  # UMD global


# ---------------------------------------------------------------------------
# ADM-15 — the Markdown editor cannot render stored HTML in the admin's session
# ---------------------------------------------------------------------------


class TestMarkdownPreviewDisabled:
    async def test_edit_form_has_no_preview(self, app):
        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/notes/1/edit")))
        # The editor is configured in admin.js (the admin CSP allows no inline
        # script); the form only marks the textarea for it.
        assert "data-hunt-markdown" in html and f'src="{P}/assets/admin.js"' in html
        assert "new EasyMDE" not in html
        script = (STATIC / "admin.js").read_text(encoding="utf-8")
        toolbar = script.split("toolbar: [", 1)[1].split("spellChecker", 1)[0]
        assert "'preview'" not in toolbar
        assert "'side-by-side'" not in toolbar
        assert "togglePreview: null" in toolbar and "toggleSideBySide: null" in toolbar
        assert "sanitizerFunction" in toolbar and "previewRender" in toolbar

    async def test_stored_markup_is_escaped_in_the_textarea(self, app):
        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/notes/1/edit")))
        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html


# ---------------------------------------------------------------------------
# ADM-L1 / ADM-L2 / ADM-L3 — media manager
# ---------------------------------------------------------------------------


class TestMediaManager:
    @pytest.fixture
    def disk(self, app, monkeypatch):
        from hunt.admin.controllers import media

        disk = MagicMock()
        disk.files.return_value = [f"media/img-{i:03d}.png" for i in range(130)]
        disk.url.side_effect = lambda p: f"/storage/{p}"
        disk.size.return_value = 10
        disk.put_file.return_value = "media/new.png"
        monkeypatch.setattr(media, "_disk", lambda: disk)
        return disk

    @pytest.mark.parametrize(
        "path",
        [
            "media/../uploads/x.jpg",
            "media/../../etc/passwd",
            "media/sub/../../uploads/x.jpg",
            "../media/x.jpg",
            "/media/x.jpg",
            "uploads/x.jpg",
            "media",
            "media/",
            "media\\..\\uploads\\x.jpg",
            "mediaevil/x.jpg",
        ],
    )
    async def test_delete_outside_the_media_directory_is_refused(self, app, disk, path):
        resp = await send(app["kernel"], post(f"{P}/media/delete", {"path": path}, expects_json=True))
        assert resp.status in (403, 422)
        disk.delete.assert_not_called()

    async def test_delete_inside_the_media_directory(self, app, disk):
        resp = await send(app["kernel"], post(f"{P}/media/delete", {"path": "media/sub/a.png"}))
        assert resp.status == 200
        disk.delete.assert_called_once_with("media/sub/a.png")

    def test_svg_is_not_an_accepted_type(self):
        from hunt.admin.controllers.media import _ALLOWED_MIME

        assert "image/svg+xml" not in _ALLOWED_MIME
        for template in ("layout.html", "media/index.html"):
            assert "svg+xml" not in (TEMPLATES / template).read_text(encoding="utf-8")

    def test_upload_rejects_svg_and_oversized_files(self, app, disk):
        from hunt.admin.controllers import media

        def upload(mime, size):
            request = MagicMock()
            request.file.return_value = MagicMock(get_mime_type=lambda: mime, size=size)
            return media.upload(request)

        with pytest.raises(HttpException) as exc:
            upload("image/svg+xml", 100)
        assert exc.value.status == 422
        app["admin"].media_max_kb = 1
        with pytest.raises(HttpException) as exc:
            upload("image/png", 2048)
        assert exc.value.status == 422
        disk.put_file.assert_not_called()
        assert upload("image/png", 512).status == 201

    async def test_listing_is_paginated(self, app, disk):
        first = json_of(await send(app["kernel"], make_request("GET", f"{P}/media/api")))
        assert len(first["files"]) == 60
        assert (first["total"], first["page"], first["last_page"]) == (130, 1, 3)
        # url()/size() are only resolved for the page that is returned
        assert disk.size.call_count == 60
        last = json_of(await send(app["kernel"], make_request("GET", f"{P}/media/api", query={"page": "3"})))
        assert len(last["files"]) == 10
        beyond = json_of(await send(app["kernel"], make_request("GET", f"{P}/media/api", query={"page": "99"})))
        assert beyond["page"] == 3

    def test_file_names_are_not_written_with_inner_html(self):
        # The media page script moved from the template to a static file.
        assert "<script>" not in (TEMPLATES / "media" / "index.html").read_text(encoding="utf-8")
        source = (STATIC / "admin-media.js").read_text(encoding="utf-8")
        for script in (source, (STATIC / "admin.js").read_text(encoding="utf-8")):
            for line in script.splitlines():
                assert not ("innerHTML" in line and "file.name" in line), line.strip()[:100]
        assert "span.textContent = part[1]" in source


# ---------------------------------------------------------------------------
# ADM-L4 / ADM-L5 — template escaping
# ---------------------------------------------------------------------------


class TestTemplateEscaping:
    def test_tojson_cannot_break_out_of_a_script_block(self):
        from hunt.admin.application import _get_env

        template = _get_env().from_string("<script>var x = {{ value | tojson }};</script>")
        html = template.render(value="</script><script>alert(1)</script>")
        assert "</script><script>" not in html
        assert html.count("</script>") == 1
        assert json.loads(html[len("<script>var x = ") : -len(";</script>")]) == "</script><script>alert(1)</script>"

    def test_tojson_still_serialises_dates(self):
        import datetime

        from hunt.admin.application import _get_env

        html = _get_env().from_string("{{ value | tojson }}").render(value={"d": datetime.date(2026, 11, 15)})
        assert "2026-11-15" in html

    async def test_confirmation_text_with_an_apostrophe_is_a_data_attribute(self, app):
        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/resources/notes")))
        assert 'data-confirm="Archive Bob&#39;s notes? It can&#39;t be undone."' in html
        assert "return confirm('Archive" not in html
        # the shared handler that reads the attribute is in admin.js, which the page loads
        assert f'src="{P}/assets/admin.js"' in html
        assert "getAttribute('data-confirm')" in (STATIC / "admin.js").read_text(encoding="utf-8")

    def test_no_template_interpolates_into_a_confirm_call(self):
        offenders = []
        for path in TEMPLATES.rglob("*.html"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "confirm(" in line and "{{" in line.split("confirm(", 1)[1].split(")", 1)[0]:
                    offenders.append(f"{path.name}: {line.strip()[:80]}")
        assert offenders == []


# ---------------------------------------------------------------------------
# ADM-L6 — sanitiser fallback escapes instead of tag-stripping
# ---------------------------------------------------------------------------


class TestSanitiserFallback:
    PAYLOAD = '<p>hi</p><img src=x onerror=alert(1) "'

    @pytest.fixture
    def no_nh3(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "nh3", None)  # makes ``import nh3`` raise ImportError

    def test_richtext_fallback_escapes(self, no_nh3):
        from hunt.admin.fields.richtext import _sanitize_html

        out = _sanitize_html(self.PAYLOAD)
        assert "<img" not in out and "<p>" not in out
        assert "&lt;img" in out

    def test_markdown_fallback_escapes(self, no_nh3):
        from hunt.admin.fields.markdown import _render_markdown

        out = _render_markdown(self.PAYLOAD)
        assert "<img" not in out and "<p>" not in out
        assert "&lt;" in out

    def test_richtext_field_value_is_safe_without_nh3(self, no_nh3):
        instance = MagicMock()
        instance._attributes = {"body": self.PAYLOAD}
        assert "<img" not in RichText("Body").value_for(instance)


# ---------------------------------------------------------------------------
# ADM-L12 — cache inspector
# ---------------------------------------------------------------------------


class TestCacheInspector:
    @pytest.fixture
    def file_cache(self, app, tmp_path):
        from hunt.cache.manager import Cache

        saved = (Cache._driver, Cache._store) if hasattr(Cache, "_driver") else None
        Cache.configure("file", path=tmp_path / "cache")
        store = Cache._get_store()
        store.put("user:1:profile", {"name": "Ada"}, 600)
        store.put("other", "value", 600)
        try:
            yield store
        finally:
            Cache.configure("array")
            if saved is not None:
                Cache._driver, Cache._store = saved

    async def test_file_driver_delete_actually_removes_the_entry(self, app, file_cache):
        from hunt.admin.controllers.cache_inspector import _list_file

        entries = _list_file(file_cache)
        assert len(entries) == 2
        target = next(e for e in entries if "Ada" in e["value"])
        # exactly what the Delete form posts back
        resp = await send(app["kernel"], post(f"{P}/cache/delete", {"key": target["key"], "file": target["file"]}))
        assert resp.status == 302
        assert file_cache.get("user:1:profile") is None
        assert file_cache.get("other") == "value"
        assert "admin_success" in app["session"].flashed

    async def test_delete_form_posts_the_file_name(self, app, file_cache):
        html = body_of(await send(app["kernel"], make_request("GET", f"{P}/cache")))
        assert html.count('name="file"') == 2

    @pytest.mark.parametrize("name", ["../../etc/passwd", "..", "a/b", "*", ""])
    def test_file_delete_cannot_escape_the_cache_directory(self, file_cache, tmp_path, name):
        from hunt.admin.controllers.cache_inspector import _delete_file_entry

        outside = tmp_path / "keep.txt"
        outside.write_text("keep")
        assert _delete_file_entry(file_cache, name) is False
        assert outside.exists()
        assert file_cache.get("other") == "value"

    def test_original_key_is_shown_when_the_payload_records_it(self, tmp_path):
        from hunt.admin.controllers.cache_inspector import _list_file

        store = MagicMock()
        store._path = tmp_path
        (tmp_path / "ab").mkdir()
        (tmp_path / "ab" / "abcdef").write_text(json.dumps({"key": "real:key", "value": 1, "expires_at": 0}))
        entries = _list_file(store)
        assert entries == [{"key": "real:key", "file": "abcdef", "ttl": None, "value": "1", "forever": True}]

    def test_redis_listing_is_bounded(self, monkeypatch):
        from hunt.admin.controllers import cache_inspector

        monkeypatch.setattr(cache_inspector, "_MAX_ENTRIES", 25)
        redis = MagicMock()
        calls = {"n": 0}

        def scan(cursor, match=None, count=None):
            calls["n"] += 1
            return cursor + 1, [f"p:key{cursor}-{i}".encode() for i in range(10)]  # never-ending keyspace

        redis.scan.side_effect = scan
        redis.ttl.return_value = 30
        redis.get.return_value = b'"v"'
        store = MagicMock(_prefix="p:", _redis=redis)
        entries = cache_inspector._list_redis(store)
        assert len(entries) == 25
        assert calls["n"] == 3

    async def test_listing_is_paginated(self, app):
        from hunt.cache.manager import Cache

        Cache.configure("array")
        store = Cache._get_store()
        for i in range(120):
            store.put(f"k{i:03d}", i, 600)
        try:
            page1 = body_of(await send(app["kernel"], make_request("GET", f"{P}/cache")))
            assert "k000" in page1 and "k049" in page1 and "k050" not in page1
            assert "120 entries" in page1
            page3 = body_of(await send(app["kernel"], make_request("GET", f"{P}/cache", query={"page": "3"})))
            assert "k119" in page3 and "k000" not in page3
        finally:
            store.flush()

    async def test_errors_are_logged_not_flashed(self, app, monkeypatch, caplog):
        from hunt.admin.controllers import cache_inspector

        store = MagicMock()
        store.flush.side_effect = RuntimeError("redis://:hunter2@10.0.0.5:6379 refused")
        monkeypatch.setattr(cache_inspector, "_store", lambda: store)
        with caplog.at_level(logging.ERROR):
            await send(app["kernel"], post(f"{P}/cache/flush"))
        assert "hunter2" not in app["session"].flashed["admin_error"]
        assert any("hunter2" in (r.exc_text or "") or r.exc_info for r in caplog.records)


# ---------------------------------------------------------------------------
# ADM-L16 — schedule monitor
# ---------------------------------------------------------------------------


class TestScheduleMonitor:
    def test_schedule_module_is_executed_once_per_file_version(self, tmp_path, monkeypatch):
        from hunt.admin.controllers import schedule as schedule_ctrl

        console = tmp_path / "app" / "console"
        console.mkdir(parents=True)
        counter = tmp_path / "runs.txt"
        schedule_file = console / "schedule.py"
        schedule_file.write_text(
            "from pathlib import Path\n"
            f"_c = Path({str(counter)!r})\n"
            "_c.write_text(str(int(_c.read_text()) + 1) if _c.exists() else '1')\n"
            "def schedule(s):\n"
            "    s.call(lambda: None).daily()\n"
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(schedule_ctrl, "_scheduler_cache", None)

        first = schedule_ctrl._load_scheduler()
        second = schedule_ctrl._load_scheduler()
        assert first is second
        assert counter.read_text() == "1"
        assert len(first.tasks) == 1

        # an edited file is picked up
        schedule_file.write_text(schedule_file.read_text() + "\n# changed\n")
        third = schedule_ctrl._load_scheduler()
        assert third is not first
        assert counter.read_text() == "2"

    def test_next_run_looks_beyond_eight_days(self):
        from datetime import datetime

        from hunt.admin.controllers.schedule import _next_run

        after = datetime(2026, 11, 15, 12, 0)
        assert _next_run("0 0 1 * *", after) == "2026-12-01 00:00"  # monthly
        assert _next_run("30 6 1 1 *", after) == "2027-01-01 06:30"  # yearly
        assert _next_run("*/15 * * * *", after) == "2026-11-15 12:15"
        assert _next_run("0 12 * * *", after) == "2026-11-16 12:00"  # strictly after "now"
        assert _next_run("59 23 * * *", datetime(2026, 12, 31, 23, 59)) == "2027-01-01 23:59"

    def test_next_run_agrees_with_a_minute_by_minute_scan(self):
        from datetime import datetime, timedelta

        from hunt.admin.controllers.schedule import _next_run
        from hunt.scheduling.cron import matches

        after = datetime(2026, 11, 15, 22, 47)
        for expression in ("5 4 * * *", "*/7 */3 * * *", "0 9 * * 1-5", "15,45 0-6 16 * *", "0 0 17 11 *"):
            probe = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
            for _ in range(60 * 24 * 10):
                if matches(expression, probe):
                    break
                probe += timedelta(minutes=1)
            assert _next_run(expression, after) == probe.strftime("%Y-%m-%d %H:%M"), expression


# ---------------------------------------------------------------------------
# ADM-L17 — logs, queue, health, gate docstring
# ---------------------------------------------------------------------------


class TestLogViewerCoercion:
    async def test_non_string_log_fields_do_not_break_search(self, app, tmp_path, monkeypatch):
        log_file = tmp_path / "hunt.log"
        log_file.write_text(
            "\n".join(
                [
                    json.dumps({"ts": 1731664800, "level": "ERROR", "message": {"event": "payment failed"}}),
                    json.dumps({"ts": "t", "level": "info", "message": 12345, "request_id": 987654}),
                    json.dumps({"ts": "t", "level": None, "message": None, "request_id": None}),
                    json.dumps(["not", "an", "object"]),
                    json.dumps({"ts": "t", "level": "info", "message": "plain text"}),
                ]
            )
        )
        monkeypatch.setenv("LOG_PATH", str(log_file))
        resp = await send(app["kernel"], make_request("GET", f"{P}/logs", query={"search": "payment"}))
        assert resp.status == 200
        assert "payment failed" in body_of(resp)
        assert "plain text" not in body_of(resp)

        resp = await send(app["kernel"], make_request("GET", f"{P}/logs", query={"search": "987654"}))
        assert resp.status == 200
        assert "12345" in body_of(resp)

    def test_parse_line_always_returns_strings(self):
        from hunt.admin.controllers.logs import _parse_line

        entry = _parse_line(json.dumps({"ts": 5, "level": 3, "message": [1, 2], "request_id": 9, "exception": {}}))
        assert all(isinstance(v, str) for v in entry.values())


class TestQueueTool:
    @pytest.fixture
    def queue_tables(self, app, monkeypatch):
        monkeypatch.setenv("APP_KEY", "k" * 40)
        run_sql(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, queue TEXT, payload TEXT, attempts INTEGER,"
            " reserved_at INTEGER, available_at INTEGER, created_at INTEGER)"
        )
        run_sql(
            "CREATE TABLE jobs_failed (id INTEGER PRIMARY KEY AUTOINCREMENT, queue TEXT, payload TEXT,"
            " exception TEXT, failed_at INTEGER)"
        )
        from sqlalchemy import text

        from hunt.database.connection import connection
        from hunt.queue.serialization import make_payload

        # A properly signed payload: retry verifies and re-signs it.
        payload = make_payload({"job": "app.jobs.demo.DemoJob", "data": {}})
        with connection().connect() as conn:
            conn.execute(
                text("INSERT INTO jobs_failed (queue, payload, exception, failed_at) VALUES ('default', :p, 'x', 1)"),
                {"p": payload},
            )
            conn.commit()
        return app

    async def test_retry_refuses_an_unverifiable_payload(self, queue_tables):
        run_sql("UPDATE jobs_failed SET payload = '{}'")
        resp = await send(queue_tables["kernel"], post(f"{P}/queue/failed/1/retry"))
        assert resp.status == 302
        assert "could not be verified" in queue_tables["session"].flashed["admin_error"]
        # nothing was enqueued and the failed job is kept for inspection
        assert rows("SELECT id FROM jobs") == []
        assert len(rows("SELECT id FROM jobs_failed")) == 1

    async def test_retry_moves_the_job(self, queue_tables):
        resp = await send(queue_tables["kernel"], post(f"{P}/queue/failed/1/retry"))
        assert resp.status == 302
        assert rows("SELECT id FROM jobs_failed") == []
        assert len(rows("SELECT id FROM jobs")) == 1

    async def test_retry_is_atomic(self, queue_tables):
        run_sql("DROP TABLE jobs")  # the re-insert will fail after the delete
        resp = await send(queue_tables["kernel"], post(f"{P}/queue/failed/1/retry"))
        assert resp.status == 302
        # the failed job is still there: nothing was lost
        assert len(rows("SELECT id FROM jobs_failed")) == 1
        flashed = queue_tables["session"].flashed["admin_error"]
        assert "jobs" not in flashed.lower() and "sql" not in flashed.lower()

    async def test_retrying_twice_does_not_duplicate(self, queue_tables):
        await send(queue_tables["kernel"], post(f"{P}/queue/failed/1/retry"))
        resp = await send(queue_tables["kernel"], post(f"{P}/queue/failed/1/retry", expects_json=True))
        assert resp.status == 404
        assert len(rows("SELECT id FROM jobs")) == 1

    async def test_errors_are_generic_and_logged(self, app, caplog):
        # no jobs_failed table at all
        with caplog.at_level(logging.ERROR):
            resp = await send(app["kernel"], post(f"{P}/queue/flush"))
        assert resp.status == 302
        flashed = app["session"].flashed["admin_error"]
        assert "no such table" not in flashed.lower()
        assert any("flush failed jobs" in r.getMessage() for r in caplog.records)


class TestHealthAndGateDocs:
    def test_health_checks_do_not_expose_exception_text(self, monkeypatch, caplog):
        from hunt.admin.controllers import health

        def boom(*a, **k):
            raise RuntimeError("could not connect to postgres://app:s3cret@db.internal/prod")

        import importlib

        monkeypatch.setattr(importlib.import_module("hunt.database.connection"), "raw", boom)
        with caplog.at_level(logging.ERROR):
            results = [health._check_database(), health._check_queue()]
        for result in results:
            assert result["ok"] is False
            assert "s3cret" not in result["detail"] and "db.internal" not in result["detail"]
        assert len(caplog.records) == 2

    def test_gate_docstring_matches_behaviour(self):
        from hunt.admin.middleware.gate import AdminGate

        doc = AdminGate.__doc__
        assert "falls back to checking Auth.check()" not in doc
        assert "denied" in doc


@pytest.mark.parametrize(
    "path",
    ["/", "/media", "/health", "/schedule", "/routes", "/queue", "/cache", "/logs", "/sessions", "/resources/notes"],
)
async def test_every_admin_page_renders(app, path):
    resp = await send(app["kernel"], make_request("GET", f"{P}{path}"))
    assert resp.status == 200
    assert "<html" in body_of(resp).lower()


async def test_create_and_show_pages_render(app):
    for path in ("/resources/notes/create", "/resources/notes/1", "/resources/notes/1/edit"):
        resp = await send(app["kernel"], make_request("GET", f"{P}{path}"))
        assert resp.status == 200, path


def test_vendored_file_hash_helper_is_sha256(build_script):
    assert build_script.sha256(b"abc") == hashlib.sha256(b"abc").hexdigest()
