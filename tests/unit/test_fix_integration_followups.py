"""Regression tests for cross-area follow-ups made while integrating the audit fixes."""

from __future__ import annotations

from datetime import datetime

from hunt.http.request import Request
from hunt.validation.form_request import FormRequest


def _scope(method: str = "POST", path: str = "/", headers: list | None = None) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    }


class TestAdminScheduleTimezone:
    def test_next_run_is_computed_in_the_task_timezone(self):
        from hunt.admin.controllers.schedule import _next_run_for
        from hunt.scheduling.scheduler import Scheduler

        scheduler = Scheduler()
        task = scheduler.call(lambda: None).cron("0 9 * * *").timezone("Asia/Tokyo")
        # 2026-01-05 23:30 UTC is already 08:30 on the 6th in Tokyo, so the next
        # 09:00 Tokyo run is on the 6th, not the 5th.
        from zoneinfo import ZoneInfo

        now = datetime(2026, 1, 5, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert _next_run_for(task, task.summary(), now) == "2026-01-06 09:00 Asia/Tokyo"

    def test_next_run_without_timezone_is_unlabelled(self):
        from hunt.admin.controllers.schedule import _next_run_for
        from hunt.scheduling.scheduler import Scheduler

        task = Scheduler().call(lambda: None).cron("0 9 * * *")
        assert _next_run_for(task, task.summary(), datetime(2026, 1, 5, 8, 0)) == "2026-01-05 09:00"


class TestBlankFormFields:
    def test_urlencoded_blank_fields_are_kept(self):
        headers = [(b"content-type", b"application/x-www-form-urlencoded")]
        request = Request(_scope(headers=headers), b"name=&nickname=ada")
        assert request.input("name") == ""
        assert request.has("name") or "name" in request.all()
        assert request.input("nickname") == "ada"


class TestCsrfMarksTheRequest:
    async def test_successful_check_sets_the_verified_marker(self):
        from hunt.http.middleware.csrf import VerifyCsrfToken
        from hunt.http.response import Response

        class _Session:
            def get(self, key, default=None):
                return "tok" if key == "_csrf_token" else default

        headers = [(b"x-csrf-token", b"tok")]
        request = Request(_scope(headers=headers), b"")
        request._session = _Session()

        async def _next(req):
            return Response("ok")

        await VerifyCsrfToken().handle(request, _next)
        assert request._csrf_verified is True


class TestFrameworkQueriesJoinTheAmbientTransaction:
    """Framework code that used to open its own connection now joins `transaction()`."""

    def _setup(self, monkeypatch, tmp_path):
        import importlib

        conn_mod = importlib.import_module("hunt.database.connection")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_CONNECTION", "sqlite")
        monkeypatch.setenv("DB_DATABASE", str(tmp_path / "app.sqlite"))
        monkeypatch.setattr(conn_mod, "_config", {})
        monkeypatch.setattr(conn_mod, "_connections", {})
        engine = conn_mod.connection()
        conn_mod.raw("CREATE TABLE people (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT)")
        return conn_mod, engine

    def test_unique_rule_sees_uncommitted_rows_of_the_same_transaction(self, monkeypatch, tmp_path):
        from hunt.validation.rules import RULES

        conn_mod, engine = self._setup(monkeypatch, tmp_path)
        try:
            with conn_mod.begin():
                conn_mod.raw("INSERT INTO people (email) VALUES ('a@x.test')")
                # Same transaction: the row is visible, and the lookup neither
                # blocks on the open write nor opens a second connection.
                assert RULES["unique"]("a@x.test", ["people", "email"], {}) is not None
                assert RULES["unique"]("b@x.test", ["people", "email"], {}) is None
        finally:
            engine.dispose()

    def test_session_registry_row_is_rolled_back_with_the_transaction(self, monkeypatch, tmp_path):
        import pytest

        from hunt.session.registry import SessionRegistry

        conn_mod, engine = self._setup(monkeypatch, tmp_path)
        conn_mod.raw(
            "CREATE TABLE user_sessions (id TEXT PRIMARY KEY, user_id TEXT, guard TEXT, ip_address TEXT,"
            " user_agent TEXT, last_active_at INTEGER)"
        )
        registry = SessionRegistry()
        try:
            with pytest.raises(RuntimeError):
                with conn_mod.begin():
                    conn_mod.raw("INSERT INTO people (email) VALUES ('a@x.test')")
                    registry.register("s" * 64, 7, "web", "127.0.0.1", "pytest")
                    raise RuntimeError("abort")
            # The registry write joined the transaction, so it rolled back too.
            assert conn_mod.raw("SELECT COUNT(*) FROM user_sessions").fetchone()[0] == 0
            assert conn_mod.raw("SELECT COUNT(*) FROM people").fetchone()[0] == 0
        finally:
            engine.dispose()


class TestUpgradeWarnsAboutUnguardedModels:
    def test_lists_only_models_missing_both_attributes(self, tmp_path):
        from hunt.console.commands.upgrade import _models_without_mass_assignment_rules

        models = tmp_path / "app" / "models"
        models.mkdir(parents=True)
        (models / "open.py").write_text(
            "from hunt.database.model import Model\n\nclass Open(Model):\n    table = 'opens'\n"
        )
        (models / "listed.py").write_text(
            "from hunt.database.model import Model\n\nclass Listed(Model):\n    fillable = ['name']\n"
        )
        (models / "guarded.py").write_text(
            "from hunt.database import model\n\nclass Guarded(model.Model):\n    guarded: list = []\n"
        )
        (models / "helper.py").write_text("class NotAModel:\n    pass\n")
        (models / "broken.py").write_text("class (:\n")

        assert _models_without_mass_assignment_rules(tmp_path) == ["app/models/open.py: Open"]

    def test_no_models_directory_is_fine(self, tmp_path):
        from hunt.console.commands.upgrade import _models_without_mass_assignment_rules

        assert _models_without_mass_assignment_rules(tmp_path) == []


class TestLodgeMinioHint:
    def test_minio_hint_mentions_the_image_override(self):
        from hunt.console.commands.lodge_install import _driver_hint

        assert "LODGE_MINIO_IMAGE" in _driver_hint("minio")
        assert "LODGE_MINIO_IMAGE" not in _driver_hint("redis")


class TestServerSideQrCode:
    URL = "otpauth://totp/Acme:ada%40example.test?secret=JBSWY3DPEHPK3PXP&issuer=Acme"

    def test_svg_is_self_contained_markup(self):
        from hunt.auth.two_factor import TwoFactor

        svg = TwoFactor.qr_code_svg(self.URL)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        assert "<path" in svg
        # Path data only: no script, no external reference, and neither the
        # secret nor the URL text is embedded in the markup.
        lowered = svg.lower()
        assert "<script" not in lowered and "href" not in lowered and "http" not in lowered
        assert "JBSWY3DPEHPK3PXP" not in svg and "otpauth" not in svg

    def test_svg_encodes_the_given_url(self):
        import segno

        from hunt.auth.two_factor import TwoFactor

        # Different inputs give different codes, and the code is the standard
        # QR symbol for the URL (same modules as a direct segno render).
        assert TwoFactor.qr_code_svg(self.URL) != TwoFactor.qr_code_svg(self.URL + "x")
        expected = segno.make(self.URL, error="m")
        size = expected.symbol_size(scale=4, border=2)[0]
        assert f'width="{size}"' in TwoFactor.qr_code_svg(self.URL)

    def test_confirm_page_renders_the_svg_unescaped_and_without_scripts(self, tmp_path):
        from markupsafe import Markup

        from hunt.auth.two_factor import TwoFactor
        from hunt.view.factory import ViewFactory

        factory = ViewFactory(str(tmp_path / "views"), str(tmp_path / "cache"))
        (tmp_path / "views").mkdir()
        html = str(
            factory.make(
                "auth.two_factor.confirm",
                {
                    "qr_url": self.URL,
                    "qr_svg": Markup(TwoFactor.qr_code_svg(self.URL)),
                    "secret": "JBSWY3DPEHPK3PXP",
                    "error": None,
                    "csrf_token": "tok",
                    "flash": {},
                },
            ).render()
        )
        assert "<svg" in html and "&lt;svg" not in html
        assert "<script" not in html
        assert "JBSWY3DPEHPK3PXP" in html  # manual-entry key is still shown


class TestLodgeMinioService:
    def test_image_is_pinned_by_digest_and_overridable(self):
        from hunt.console.commands.lodge_install import _IMAGE_MINIO, _build_compose

        assert "@sha256:" in _IMAGE_MINIO
        compose = _build_compose("myapp", "3.12", ["minio"])
        assert f"${{LODGE_MINIO_IMAGE:-{_IMAGE_MINIO}}}" in compose

    def test_service_definition_is_valid_and_image_agnostic(self):
        import yaml

        from hunt.console.commands.lodge_install import _build_compose

        service = yaml.safe_load(_build_compose("myapp", "3.12", ["minio"]))["services"]["minio"]
        # Explicit entrypoint, so the command does not depend on the image's own.
        assert service["entrypoint"] == ["minio"]
        assert service["command"] == ["server", "/data", "--console-address", ":9001"]
        # The health check uses a tool that exists in the image.
        assert service["healthcheck"]["test"] == ["CMD", "mc", "ready", "local"]
        assert all(str(port).startswith("127.0.0.1:") for port in service["ports"])


class TestLodgeCredentials:
    def test_compose_has_no_hard_coded_credential_fallbacks(self):
        from hunt.console.commands.lodge_install import _build_compose

        compose = _build_compose("myapp", "3.12", ["pgsql", "redis", "mailpit", "minio"])
        compose += _build_compose("myapp", "3.12", ["mysql"])
        for fallback in (":-secret", ":-password", ":-lodge"):
            assert fallback not in compose
        # A missing value stops `docker compose` with a clear message instead.
        assert "${DB_PASSWORD:?Set DB_PASSWORD in .env}" in compose
        assert "${AWS_SECRET_ACCESS_KEY:?Set AWS_SECRET_ACCESS_KEY in .env}" in compose

    def test_redis_requires_a_password(self):
        import yaml

        from hunt.console.commands.lodge_install import _build_compose

        redis = yaml.safe_load(_build_compose("myapp", "3.12", ["redis"]))["services"]["redis"]
        assert redis["command"][:2] == ["redis-server", "--requirepass"]
        assert "REDIS_PASSWORD" in redis["command"][2]
        assert "REDIS_PASSWORD" in redis["environment"]["REDISCLI_AUTH"]

    def test_redis_password_is_generated_and_preserved(self):
        from hunt.console.commands.lodge_install import _apply_service_env, _get_env

        content, _ = _apply_service_env("", "redis", generate_secrets=True)
        generated = _get_env(content, "REDIS_PASSWORD")
        assert generated and generated != "secret" and len(generated) >= 16

        content, kept = _apply_service_env("REDIS_PASSWORD=mine\n", "redis", generate_secrets=True)
        assert _get_env(content, "REDIS_PASSWORD") == "mine"
        assert "REDIS_PASSWORD" in kept


class TestPasswordResetIsAtomic:
    def _setup(self, monkeypatch, tmp_path):
        import importlib

        from hunt.auth.passwords import PasswordBroker
        from hunt.database.model import Model

        conn_mod = importlib.import_module("hunt.database.connection")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_CONNECTION", "sqlite")
        monkeypatch.setenv("DB_DATABASE", str(tmp_path / "app.sqlite"))
        monkeypatch.setattr(conn_mod, "_config", {})
        monkeypatch.setattr(conn_mod, "_connections", {})
        engine = conn_mod.connection()
        conn_mod.raw(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, password TEXT,"
            " created_at INTEGER, updated_at INTEGER)"
        )
        conn_mod.raw("INSERT INTO users (email, password) VALUES ('ada@example.test', 'old-hash')")

        class ResetUser(Model):
            table = "users"
            fillable = ["email"]  # noqa: RUF012

        broker = PasswordBroker()
        broker.set_model(ResetUser)
        conn_mod.raw(f"CREATE TABLE {broker.TABLE} (email TEXT, token TEXT, created_at INTEGER)")
        _, token = broker.create_token("ada@example.test")
        return conn_mod, engine, broker, ResetUser, token

    def test_failed_save_rolls_back_and_keeps_the_token_usable(self, monkeypatch, tmp_path):
        import pytest

        conn_mod, engine, broker, user_model, token = self._setup(monkeypatch, tmp_path)
        credentials = {"email": "ada@example.test", "token": token, "password": "a-new-password"}
        try:

            def boom(self):
                raise RuntimeError("database went away")

            original_save = user_model.save
            monkeypatch.setattr(user_model, "save", boom)
            with pytest.raises(RuntimeError):
                broker.reset(credentials, revoke_sessions=False)
            # Nothing changed: the token row is back and the password is the old one.
            assert conn_mod.raw(f"SELECT COUNT(*) FROM {broker.TABLE}").fetchone()[0] == 1
            assert conn_mod.raw("SELECT password FROM users").fetchone()[0] == "old-hash"

            monkeypatch.setattr(user_model, "save", original_save)
            assert broker.reset(credentials, revoke_sessions=False) is True
            assert conn_mod.raw(f"SELECT COUNT(*) FROM {broker.TABLE}").fetchone()[0] == 0
            assert conn_mod.raw("SELECT password FROM users").fetchone()[0] != "old-hash"
            # Single use: the same link does not work twice.
            assert broker.reset(credentials, revoke_sessions=False) is False
        finally:
            engine.dispose()


class TestLogMailerBodies:
    def _send(self, monkeypatch, config, app_env):
        from hunt.mail import manager
        from hunt.mail.mailable import Mailable

        class Reset(Mailable):
            def build(self):
                return self.subject("Reset your password").text("link: https://app.test/reset/TOKEN123")

        if app_env is None:
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", app_env)
        logged = []
        monkeypatch.setattr(manager.Log, "info", lambda message, **context: logged.append(message))
        mailable = Reset().to("ada@example.test")
        mailable.build()
        manager._LogDriver().send(mailable, config)
        return logged[0]

    def test_body_is_logged_in_local_development_by_default(self, monkeypatch):
        assert "TOKEN123" in self._send(monkeypatch, {}, "local")

    def test_body_is_not_logged_outside_local_by_default(self, monkeypatch):
        assert "TOKEN123" not in self._send(monkeypatch, {}, "production")
        assert "TOKEN123" not in self._send(monkeypatch, {}, None)  # unset means production

    def test_explicit_setting_wins_either_way(self, monkeypatch):
        assert "TOKEN123" not in self._send(monkeypatch, {"log_body": False}, "local")
        assert "TOKEN123" in self._send(monkeypatch, {"log_body": True}, "production")


class TestUpgradeAddsMiddleware:
    OLD = (
        "from hunt.http.middleware.session import StartSession\n"
        "from hunt.http.middleware.csrf import VerifyCsrfToken\n"
        "\n"
        "# -- Global middleware\n"
        "global_middleware = [\n"
        "    StartSession,\n"
        "    VerifyCsrfToken,\n"
        "]\n"
    )

    def _middleware(self, source):
        import ast

        tree = ast.parse(source)
        assign = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "global_middleware")
        return [element.id for element in assign.value.elts]

    def test_maintenance_mode_is_added_first(self):
        from hunt.console.commands.upgrade import _MAINTENANCE_LABEL, _plan_bootstrap

        content, patches, _ = _plan_bootstrap(self.OLD, include_auth=False)
        assert _MAINTENANCE_LABEL in patches
        assert self._middleware(content) == ["MaintenanceMode", "StartSession", "VerifyCsrfToken"]
        assert "from hunt.http.middleware.maintenance import MaintenanceMode\n" in content
        compile(content, "bootstrap/app.py", "exec")

    def test_secure_headers_only_on_request_and_in_the_right_order(self):
        from hunt.console.commands.upgrade import _SECURE_HEADERS_LABEL, _plan_bootstrap

        content, patches, _ = _plan_bootstrap(self.OLD, include_auth=False)
        assert _SECURE_HEADERS_LABEL not in patches and "SecureHeaders" not in content

        imp = "from app.middleware.secure_headers import SecureHeaders"
        content, patches, _ = _plan_bootstrap(self.OLD, include_auth=False, secure_headers_import=imp)
        assert _SECURE_HEADERS_LABEL in patches
        assert self._middleware(content) == ["MaintenanceMode", "SecureHeaders", "StartSession", "VerifyCsrfToken"]
        assert imp in content

    def test_already_wired_bootstrap_is_left_alone(self):
        from hunt.console.commands.upgrade import _MAINTENANCE_LABEL, _plan_bootstrap

        once, _, _ = _plan_bootstrap(self.OLD, include_auth=False)
        twice, patches, manual = _plan_bootstrap(once, include_auth=False)
        assert twice == once
        assert _MAINTENANCE_LABEL not in patches and _MAINTENANCE_LABEL not in manual

    def test_unusual_list_layout_is_reported_not_guessed(self):
        from hunt.console.commands.upgrade import _MAINTENANCE_LABEL, _plan_bootstrap

        source = "global_middleware = [StartSession, VerifyCsrfToken]\n"
        content, patches, manual = _plan_bootstrap(source, include_auth=False)
        assert content == source
        assert _MAINTENANCE_LABEL in manual and _MAINTENANCE_LABEL not in patches


class TestQueuePruneProcessedIsRegistered:
    def test_command_is_available_from_the_cli(self):
        from click.testing import CliRunner

        from hunt.console.kernel import cli

        assert "queue:prune-processed" in cli.commands
        result = CliRunner().invoke(cli, ["queue:prune-processed", "--help"])
        assert result.exit_code == 0


class TestSessionHashIdsIsRegistered:
    def test_command_is_available_from_the_cli(self):
        from click.testing import CliRunner

        from hunt.console.kernel import cli

        assert "session:hash-ids" in cli.commands
        assert CliRunner().invoke(cli, ["session:hash-ids", "--help"]).exit_code == 0


class TestDebugPanelRespectsContentSecurityPolicy:
    async def _render(self, monkeypatch, headers):
        from hunt.http.middleware import debug_panel
        from hunt.http.response import Response

        monkeypatch.setattr(debug_panel, "_is_debug", lambda: True)
        request = Request(_scope(method="GET"), b"")

        async def _next(req):
            response = Response("<html><body><p>page</p></body></html>")
            for name, value in headers.items():
                response.header(name, value)
            return response

        response = await debug_panel.DebugPanel().handle(request, _next)
        return response._body.decode()

    async def test_panel_is_injected_without_a_policy(self, monkeypatch):
        assert "_hunt_dbg" in await self._render(monkeypatch, {})

    async def test_panel_is_injected_when_inline_scripts_are_allowed(self, monkeypatch):
        policy = "default-src 'self'; script-src 'self' 'unsafe-inline'"
        assert "_hunt_dbg" in await self._render(monkeypatch, {"Content-Security-Policy": policy})

    async def test_panel_is_left_out_under_a_strict_policy(self, monkeypatch):
        for policy in ("default-src 'self'; script-src 'self'", "default-src 'self'"):
            html = await self._render(monkeypatch, {"Content-Security-Policy": policy})
            assert "_hunt_dbg" not in html and "<p>page</p>" in html


class _StorePost(FormRequest):
    def authorize(self) -> bool:
        return True

    def rules(self) -> dict:
        return {"title": "required|string"}


class TestFormRequestNamedRequest:
    async def test_parameter_named_request_can_be_a_form_request(self):
        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router

        seen = {}

        def store(request: _StorePost):
            seen["type"] = type(request).__name__
            return {"title": request.validated()["title"]}

        def plain(request):
            seen["plain"] = type(request).__name__
            return {"ok": True}

        router = Router()
        router.post("/posts", store)
        router.post("/plain", plain)
        kernel = HttpKernel(router)

        async def call(path, body):
            sent = []
            messages = [{"type": "http.request", "body": body, "more_body": False}]

            async def receive():
                return messages.pop(0) if messages else {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            headers = [(b"content-type", b"application/json"), (b"accept", b"application/json")]
            scope = _scope(path=path, headers=headers) | {"http_version": "1.1", "raw_path": path.encode()}
            await kernel(scope, receive, send)
            return next(m["status"] for m in sent if m["type"] == "http.response.start")

        assert await call("/posts", b'{"title": "Hello"}') == 200
        assert seen["type"] == "_StorePost"
        assert await call("/posts", b"{}") == 422  # the form request validated the input
        assert await call("/plain", b"{}") == 200
        assert seen["plain"] == "Request"


class TestGeneratedSessionConfigDefersToTheFramework:
    def _secure(self, monkeypatch, tmp_path, env_value):
        import importlib.util

        from hunt.console.commands import new as new_module

        source = next(v for k, v in vars(new_module).items() if k.startswith("_CONFIG_SESSION") and isinstance(v, str))
        path = tmp_path / "session_config.py"
        path.write_text(source)
        if env_value is None:
            monkeypatch.delenv("SESSION_SECURE", raising=False)
        else:
            monkeypatch.setenv("SESSION_SECURE", env_value)
        spec = importlib.util.spec_from_file_location(f"generated_session_{env_value}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.config["secure"]

    def test_unset_leaves_the_decision_to_the_framework(self, monkeypatch, tmp_path):
        assert self._secure(monkeypatch, tmp_path, None) is None
        assert self._secure(monkeypatch, tmp_path, "") is None

    def test_explicit_values_force_it(self, monkeypatch, tmp_path):
        assert self._secure(monkeypatch, tmp_path, "true") is True
        assert self._secure(monkeypatch, tmp_path, "False") is False


class TestModelFollowUps:
    def _db(self, monkeypatch):
        import importlib

        conn_mod = importlib.import_module("hunt.database.connection")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_CONNECTION", "sqlite")
        monkeypatch.setenv("DB_DATABASE", ":memory:")
        monkeypatch.setattr(conn_mod, "_config", {})
        monkeypatch.setattr(conn_mod, "_connections", {})
        conn_mod.raw("CREATE TABLE fu_countries (code TEXT PRIMARY KEY, name TEXT)")
        conn_mod.raw("CREATE TABLE fu_authors (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
        conn_mod.raw("CREATE TABLE fu_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, fu_author_id INTEGER, title TEXT)")
        return conn_mod

    def test_primary_key_is_fillable_only_when_listed(self, monkeypatch):
        from hunt.database.model import Model

        conn_mod = self._db(monkeypatch)

        class Country(Model):
            table = "fu_countries"
            primary_key = "code"
            timestamps = False
            fillable = ["code", "name"]  # noqa: RUF012

        class Open(Model):
            table = "fu_authors"
            timestamps = False
            guarded: list = []  # noqa: RUF012

        country = Country.create({"code": "NL", "name": "Netherlands"})
        assert country.code == "NL"
        assert Country.find("NL").name == "Netherlands"
        # Not listed: the key stays protected even on a fully unguarded model.
        assert Open({"id": 999, "name": "x"})._attributes.get("id") is None
        conn_mod.connection().dispose()

    def test_related_returns_eager_loaded_models_without_another_query(self, monkeypatch):
        from hunt.database.model import Model

        conn_mod = self._db(monkeypatch)

        class FuAuthor(Model):
            table = "fu_authors"
            timestamps = False
            fillable = ["name"]  # noqa: RUF012

        class FuPost(Model):
            table = "fu_posts"
            timestamps = False
            fillable = ["fu_author_id", "title"]  # noqa: RUF012

            def author(self):
                return self.belongs_to(FuAuthor, "fu_author_id")

        ada = FuAuthor.create({"name": "Ada"})
        FuPost.create({"fu_author_id": ada.id, "title": "One"})

        posts = FuPost.query().with_("author").get()
        assert posts[0].related("author").name == "Ada"
        # Lazy path: loaded on first use, then kept.
        post = FuPost.query().first()
        first = post.related("author")
        assert first.name == "Ada" and post.related("author") is first
        conn_mod.connection().dispose()

    def test_remember_uses_the_given_key(self, monkeypatch):
        from hunt.cache import manager as cache_manager
        from hunt.database.query_builder import QueryBuilder

        conn_mod = self._db(monkeypatch)
        conn_mod.raw("INSERT INTO fu_authors (name) VALUES ('Ada')")
        stored = {}

        class _Cache:
            @staticmethod
            def get(key, default=None):
                return stored.get(key, default)

            @staticmethod
            def put(key, value, seconds=None):
                stored[key] = value

        monkeypatch.setattr(cache_manager, "Cache", _Cache)
        rows = QueryBuilder("fu_authors").remember(60, key="authors.all").get()
        assert len(rows) == 1
        assert list(stored) == ["authors.all"]
        conn_mod.connection().dispose()


class _GatePost:
    pass


class TestSmallFrameworkFollowUps:
    def test_policy_class_level_ability_takes_only_the_user(self):
        from hunt.auth.gate import Gate

        class PostPolicy:
            def create(self, user):
                return user == "editor"

            def view_any(self, user, model=None):
                return model is _GatePost

            def update(self, user, post):
                return isinstance(post, _GatePost)

        gate = type(Gate)() if not isinstance(Gate, type) else Gate()
        gate.policy(_GatePost, PostPolicy)
        assert gate.for_user("editor").allows("create", _GatePost) is True
        assert gate.for_user("reader").allows("create", _GatePost) is False
        # A method that accepts the class still receives it.
        assert gate.for_user("editor").allows("view_any", _GatePost) is True
        assert gate.for_user("editor").allows("update", _GatePost()) is True

    def test_auth_directive_works_with_a_shared_helper_function(self, tmp_path):
        from hunt.view.factory import ViewFactory

        views = tmp_path / "views"
        views.mkdir()
        (views / "page.html").write_text("@auth in @endauth@guest out @endguest")
        factory = ViewFactory(str(views), str(tmp_path / "cache"))

        def render(value):
            return str(factory.make("page", {"auth_user": value}).render()).strip()

        assert render(lambda: None) == "out"  # a helper that reports "nobody logged in"
        assert render(lambda: object()) == "in"
        assert render(None) == "out"
        assert render(object()) == "in"

    def test_mail_log_body_env_is_honoured_without_config(self, monkeypatch):
        from hunt.mail import manager
        from hunt.mail.mailable import Mailable

        class Note(Mailable):
            def build(self):
                return self.subject("s").text("BODY-TEXT")

        logged = []
        monkeypatch.setattr(manager.Log, "info", lambda message, **context: logged.append(message))
        monkeypatch.setenv("APP_ENV", "production")
        mailable = Note().to("a@example.test")
        mailable.build()
        monkeypatch.setenv("MAIL_LOG_BODY", "true")
        manager._LogDriver().send(mailable, {})
        monkeypatch.setenv("MAIL_LOG_BODY", "false")
        monkeypatch.setenv("APP_ENV", "local")
        manager._LogDriver().send(mailable, {})
        assert "BODY-TEXT" in logged[0] and "BODY-TEXT" not in logged[1]

    def test_database_notification_uses_the_notifiable_route(self):
        from hunt.notifications.channels.database import DatabaseChannel

        class Team:
            _attributes = {"id": 1}  # noqa: RUF012

            def route_notification_for_database(self):
                return "team-42"

        class Plain:
            _attributes = {"id": 7}  # noqa: RUF012

        assert DatabaseChannel._get_id(Team()) == "team-42"
        assert DatabaseChannel._get_id(Plain()) == 7

    def test_log_exception_without_argument_logs_the_active_traceback(self, monkeypatch):
        import logging

        from hunt.log.manager import Log

        monkeypatch.delenv("LOG_CHANNEL", raising=False)
        monkeypatch.setattr(Log, "_default_logger", None)
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        logging.getLogger("hunt").addHandler(handler)
        try:
            try:
                raise ValueError("boom")
            except ValueError:
                Log.exception("failed")
        finally:
            logging.getLogger("hunt").removeHandler(handler)
        assert records and records[-1].exc_info and records[-1].exc_info[0] is ValueError

    def test_new_app_ships_the_jobs_processed_migration(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.new import new_command

        monkeypatch.chdir(tmp_path)
        assert CliRunner().invoke(new_command, ["demo"]).exit_code == 0
        migration = tmp_path / "demo" / "database" / "migrations" / "0005_create_jobs_processed_table.py"
        source = migration.read_text()
        assert "jobs_processed" in source
        compile(source, str(migration), "exec")

    def test_migration_guard_treats_unset_app_env_as_production(self, monkeypatch):
        from hunt.console.commands.db.boot import is_production

        monkeypatch.delenv("APP_ENV", raising=False)
        assert is_production() is True
        monkeypatch.setenv("APP_ENV", "local")
        assert is_production() is False


class TestAdminFollowUps:
    def test_multi_word_field_names_map_to_snake_case_columns(self):
        from hunt.admin.fields.text import Text

        assert Text("Cover Photo").attribute == "cover_photo"
        assert Text("Created At").attribute == "created_at"
        assert Text("Name").attribute == "name"
        assert Text("CoverPhoto").attribute == "cover_photo"
        assert Text("Cover Photo", attribute="img").attribute == "img"

    def test_record_outside_index_query_is_not_found(self):
        from unittest.mock import MagicMock

        import pytest

        from hunt.admin.controllers.resource import _get_instance
        from hunt.http.response import HttpException

        instance = MagicMock()
        instance._attributes = {"id": 5}
        resource = MagicMock()
        resource.model.primary_key = "id"
        resource.model.find.return_value = instance
        request = MagicMock()

        resource.index_query.return_value.where.return_value.exists.return_value = True
        assert _get_instance(resource, "5", request) is instance
        resource.index_query.return_value.where.assert_called_with("id", 5)

        resource.index_query.return_value.where.return_value.exists.return_value = False
        with pytest.raises(HttpException) as excinfo:
            _get_instance(resource, "5", request)
        assert excinfo.value.status_code == 404 if hasattr(excinfo.value, "status_code") else True

    def test_publish_skips_existing_files_and_adds_missing_ones(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.admin.console.publish import admin_publish_command

        monkeypatch.chdir(tmp_path)
        dest = tmp_path / "resources" / "views" / "admin"
        dest.mkdir(parents=True)
        (dest / "layout.html").write_text("CUSTOM")
        result = CliRunner().invoke(admin_publish_command, [])
        assert result.exit_code == 0
        assert (dest / "layout.html").read_text() == "CUSTOM"  # kept
        assert (dest / "dashboard.html").exists()  # missing files are published
        assert "skipped" in result.output

        result = CliRunner().invoke(admin_publish_command, ["--force"])
        assert (dest / "layout.html").read_text() != "CUSTOM"

    def test_admin_render_is_public_and_sets_the_policy(self, monkeypatch):
        from hunt.admin.application import Admin
        from hunt.http.response import Response

        monkeypatch.setattr(Admin, "_base_context", lambda request: {"prefix": "/hunt-admin"})
        captured = {}

        def fake_render(template_name, context, status=200):
            captured.update(template=template_name, context=context, status=status)
            return Response("ok", status=status)

        monkeypatch.setattr(Admin, "_render", fake_render)
        Admin.render(object(), "admin/reports.html", {"title": "Reports"})
        assert captured["template"] == "admin/reports.html"
        assert captured["context"] == {"prefix": "/hunt-admin", "title": "Reports"}


class TestTinkerBootsTheAppWithoutIPython:
    def test_fallback_repl_runs_after_bootstrapping(self, tmp_path, monkeypatch):
        import builtins
        import sys

        from click.testing import CliRunner

        from hunt.console.commands.tinker import tinker_command

        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "__init__.py").write_text("")
        (tmp_path / "bootstrap" / "app.py").write_text("application = 'booted'\nopen('booted.txt', 'w').close()\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", list(sys.path))

        def is_bootstrap(name):
            return name == "bootstrap" or name.startswith("bootstrap.")

        # Put sys.modules back exactly as it was afterwards: the command imports
        # this test's throwaway `bootstrap` package, which must not be seen by
        # later tests.
        saved = {name: module for name, module in sys.modules.items() if is_bootstrap(name)}
        for name in saved:
            del sys.modules[name]

        real_import = builtins.__import__

        def no_ipython(name, *args, **kwargs):
            if name == "IPython" or name.startswith("traitlets"):
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_ipython)
        started = {}
        monkeypatch.setattr("code.interact", lambda **kwargs: started.update(kwargs))
        try:
            result = CliRunner().invoke(tinker_command, [])
        finally:
            for name in [m for m in sys.modules if is_bootstrap(m)]:
                del sys.modules[name]
            sys.modules.update(saved)

        assert result.exit_code == 0, result.output
        assert (tmp_path / "booted.txt").exists()  # the app was bootstrapped
        assert "hunt-framework[tinker]" in result.output
        assert {"dd", "dump"} <= set(started["local"])
