"""Regression tests for the scaffold and the make:* generators (GEN-*, CLI-01, CLI-L2, CLI-L3, CLI-L5)."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

import hunt
from hunt.console.commands.make._naming import migration_timestamp, validate_identifier
from hunt.console.commands.make.api_scaffold import make_api_command
from hunt.console.commands.make.command import make_command_command
from hunt.console.commands.make.controller import make_controller_command
from hunt.console.commands.make.crud import make_crud_command
from hunt.console.commands.make.event import make_event_command
from hunt.console.commands.make.factory import make_factory_command
from hunt.console.commands.make.job import make_job_command
from hunt.console.commands.make.listener import make_listener_command
from hunt.console.commands.make.mail import make_mail_command
from hunt.console.commands.make.middleware import make_middleware_command
from hunt.console.commands.make.migration import make_migration_command
from hunt.console.commands.make.model import make_model_command
from hunt.console.commands.make.notification import make_notification_command
from hunt.console.commands.make.observer import make_observer_command
from hunt.console.commands.make.policy import make_policy_command
from hunt.console.commands.make.request import make_request_command
from hunt.console.commands.make.resource import make_resource_command
from hunt.console.commands.make.rule import make_rule_command
from hunt.console.commands.make.seeder import make_seeder_command
from hunt.console.commands.make.test import make_test_command
from hunt.console.commands.make.two_factor import make_two_factor_command
from hunt.console.commands.new import framework_requirement, new_command
from hunt.console.commands.session_table import session_table_command

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTERS = [None, "blog", "api", "saas"]


def _new(*args: str, input: str | None = None):
    return CliRunner().invoke(new_command, list(args), input=input)


@pytest.fixture()
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def app(cwd, monkeypatch):
    result = _new("myapp")
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(cwd / "myapp")
    return cwd / "myapp"


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


# ---------------------------------------------------------------------------
# Every generated file is valid
# ---------------------------------------------------------------------------


class TestGeneratedFilesAreValid:
    @pytest.mark.parametrize("starter", STARTERS)
    def test_python_compiles_and_pyproject_parses(self, cwd, starter):
        args = ["myapp"] + ([f"--starter={starter}"] if starter else [])
        result = _new(*args)
        assert result.exit_code == 0, result.output
        root = cwd / "myapp"

        python_files = list(root.rglob("*.py"))
        assert len(python_files) > 20
        for file in python_files:
            compile(file.read_text(encoding="utf-8"), str(file), "exec")

        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        assert project["name"] == "myapp"
        if starter == "saas":
            assert any(dep.startswith("stripe") for dep in project["optional-dependencies"]["billing"])

    def test_generators_emit_valid_python(self, app):
        runner = CliRunner()
        fields = "title:string body:text published:boolean views:integer price:decimal day:date ref:uuid"
        commands = [
            (make_crud_command, ["Article", "--fields", fields]),
            (make_api_command, ["Product", "--fields", fields]),
            (make_model_command, ["Invoice", "--all", "--fields", "number:string"]),
            (make_model_command, ["Blank"]),
            (make_migration_command, ["add_notes_to_invoices", "--table", "invoices"]),
            (make_migration_command, ["cleanup"]),
            (make_controller_command, ["ReportController", "--resource"]),
            (make_middleware_command, ["EnsureTeam"]),
            (make_mail_command, ["WelcomeMail"]),
            (make_notification_command, ["InvoicePaid"]),
            (make_event_command, ["OrderShipped"]),
            (make_listener_command, ["SendReceipt", "--event", "OrderShipped"]),
            (make_job_command, ["PruneThings"]),
            (make_observer_command, ["InvoiceObserver", "--model", "Invoice"]),
            (make_policy_command, ["ArticlePolicy", "--model", "Article"]),
            (make_request_command, ["StoreArticle"]),
            (make_resource_command, ["InvoiceResource"]),
            (make_rule_command, ["Uppercase"]),
            (make_seeder_command, ["Demo"]),
            (make_factory_command, ["Article"]),
            (make_test_command, ["Home", "--feature"]),
            (make_command_command, ["SendEmails"]),
            (make_two_factor_command, []),
            (session_table_command, []),
        ]
        for command, args in commands:
            result = runner.invoke(command, args)
            assert result.exit_code == 0, f"{command.name} {args}: {result.output}"
        for file in app.rglob("*.py"):
            compile(file.read_text(encoding="utf-8"), str(file), "exec")


# ---------------------------------------------------------------------------
# GEN-02
# ---------------------------------------------------------------------------


class TestFrameworkDependency:
    def test_requirement_tracks_the_running_minor_series(self):
        major, minor = hunt.__version__.split(".")[:2]
        assert framework_requirement() == f"hunt-framework>={major}.{minor},<{major}.{int(minor) + 1}"
        assert framework_requirement("1.9.3") == "hunt-framework>=1.9,<1.10"

    def test_generated_dependency_is_the_frameworks_distribution_name(self, app):
        own_name = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["name"]
        deps = tomllib.loads((app / "pyproject.toml").read_text())["project"]["dependencies"]
        names = [re.split(r"[<>=!~ \[;]", dep, maxsplit=1)[0] for dep in deps]
        assert names == [own_name]
        assert "hunt" not in names
        assert deps[0] == framework_requirement()


# ---------------------------------------------------------------------------
# CLI-01: hunt new
# ---------------------------------------------------------------------------


class TestNewNameHandling:
    @pytest.mark.parametrize(
        "name",
        [".", "..", "../escape", "a/b", "/tmp/hunt-abs-target", "-x", "my app", "x;y", "trailing.", ".hidden"],
    )
    def test_bad_names_create_and_delete_nothing(self, cwd, name):
        (cwd / "keep.txt").write_text("keep")
        before = _tree(cwd)
        result = _new("--force", "--", name)
        assert result.exit_code != 0
        assert _tree(cwd) == before
        assert not Path("/tmp/hunt-abs-target").exists()

    def test_force_on_cwd_does_not_delete_it(self, cwd):
        (cwd / ".git").mkdir()
        (cwd / ".git" / "HEAD").write_text("ref")
        result = _new(".", "--force")
        assert result.exit_code != 0
        assert (cwd / ".git" / "HEAD").read_text() == "ref"

    def test_existing_directory_needs_force(self, cwd):
        (cwd / "myapp").mkdir()
        (cwd / "myapp" / "notes.txt").write_text("mine")
        result = _new("myapp")
        assert result.exit_code == 1
        assert (cwd / "myapp" / "notes.txt").read_text() == "mine"

    def test_force_refuses_a_foreign_directory_without_confirmation(self, cwd):
        (cwd / "myapp").mkdir()
        (cwd / "myapp" / "notes.txt").write_text("mine")
        result = _new("myapp", "--force")  # no terminal: the prompt cannot be answered
        assert result.exit_code == 1
        assert (cwd / "myapp" / "notes.txt").read_text() == "mine"

        declined = _new("myapp", "--force", input="n\n")
        assert declined.exit_code == 1
        assert (cwd / "myapp" / "notes.txt").exists()

    def test_force_replaces_a_foreign_directory_after_confirmation(self, cwd):
        (cwd / "myapp").mkdir()
        (cwd / "myapp" / "notes.txt").write_text("mine")
        result = _new("myapp", "--force", input="y\n")
        assert result.exit_code == 0, result.output
        assert not (cwd / "myapp" / "notes.txt").exists()
        assert (cwd / "myapp" / "bootstrap" / "app.py").exists()

    def test_force_replaces_a_previous_scaffold_without_asking(self, cwd):
        assert _new("myapp").exit_code == 0
        (cwd / "myapp" / "extra.txt").write_text("x")
        result = _new("myapp", "--force")
        assert result.exit_code == 0, result.output
        assert not (cwd / "myapp" / "extra.txt").exists()

    def test_force_never_follows_a_symlink(self, cwd):
        victim = cwd / "victim"
        victim.mkdir()
        (victim / "data.txt").write_text("precious")
        (cwd / "myapp").symlink_to(victim, target_is_directory=True)
        result = _new("myapp", "--force", input="y\n")
        assert result.exit_code != 0
        assert (victim / "data.txt").read_text() == "precious"


# ---------------------------------------------------------------------------
# Skeleton contents: GEN-08/09/10/12, GEN-L1/L4/L5, CLI-L4
# ---------------------------------------------------------------------------


class TestSkeleton:
    def test_env_file_is_private_and_has_a_key(self, app):
        env = app / ".env"
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
        assert re.search(r"^APP_KEY=base64:.+$", env.read_text(), re.M)
        assert re.search(r"^APP_KEY=$", (app / ".env.example").read_text(), re.M)

    @pytest.mark.parametrize("ignore_file", [".gitignore", ".dockerignore"])
    def test_ignore_files_cover_runtime_state(self, app, ignore_file):
        lines = (app / ignore_file).read_text().splitlines()
        for entry in (
            ".env",
            "storage/framework/sessions/*",
            "storage/framework/cache/*",
            "storage/framework/config.json",
            "storage/app/*",
            ".maintenance",
        ):
            assert entry in lines, f"{entry} missing from {ignore_file}"

    def test_gitkeep_files_preserve_runtime_directories(self, app):
        for directory in (
            "storage/framework/sessions",
            "storage/framework/cache",
            "storage/logs",
            "storage/app/public",
        ):
            assert (app / directory / ".gitkeep").is_file()

    def test_git_does_not_pick_up_runtime_state(self, app):
        git = shutil.which("git")
        if git is None:
            pytest.skip("git is not installed")
        (app / "storage/framework/sessions/abc123").write_text("session")
        (app / "storage/framework/cache/entry").write_text("cache")
        (app / "storage/framework/config.json").write_text("{}")
        (app / "storage/app/upload.bin").write_text("upload")
        (app / "storage/logs/hunt.log").write_text("log")
        (app / ".maintenance").write_text("{}")
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(app.parent), "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run([git, "-C", str(app), "init", "-q"], check=True, env=env, capture_output=True)
        out = subprocess.run(
            [git, "-C", str(app), "add", "-A", "--dry-run"], check=True, env=env, capture_output=True, text=True
        ).stdout
        staged = re.findall(r"add '([^']+)'", out)
        assert "bootstrap/app.py" in staged
        assert "storage/framework/sessions/.gitkeep" in staged
        for leaked in (
            ".env",
            ".maintenance",
            "storage/framework/sessions/abc123",
            "storage/framework/cache/entry",
            "storage/framework/config.json",
            "storage/app/upload.bin",
            "storage/logs/hunt.log",
        ):
            assert leaked not in staged

    def test_dockerfile_is_hardened(self, app):
        dockerfile = (app / "Dockerfile").read_text()
        assert len(re.findall(r"^FROM ", dockerfile, re.M)) == 2
        assert re.search(r"python:3\.\d+\.\d+-slim-\w+@sha256:[0-9a-f]{64}", dockerfile)
        runtime = dockerfile.split("# -- Runtime stage", 1)[1]
        assert "gcc" not in runtime
        assert re.search(r"^USER app$", runtime, re.M)
        assert "chown -R app:app storage" in runtime
        assert "HEALTHCHECK" in runtime and "/health" in runtime
        assert 'CMD ["hunt", "serve:production", "--host", "0.0.0.0", "--port", "8000"]' in runtime
        assert not re.search(r"^CMD hunt", dockerfile, re.M)

    def test_bootstrap_registers_maintenance_and_security_middleware(self, app):
        source = (app / "bootstrap" / "app.py").read_text()
        block = source.split("global_middleware = [", 1)[1].split("]", 1)[0]
        active = [line.strip().rstrip(",") for line in block.splitlines() if line.strip() and "#" not in line]
        assert active == ["MaintenanceMode", "SecureHeaders", "StartSession", "VerifyCsrfToken"]
        assert "# TrustProxies," in block

    def test_csrf_exemptions_per_starter(self, cwd):
        for starter, expected in ((None, "[]"), ("api", '["/api"]'), ("saas", '["/api/billing/webhook"]')):
            name = f"app{starter or 'base'}"
            assert _new(name, *([f"--starter={starter}"] if starter else [])).exit_code == 0
            source = (cwd / name / "app" / "middleware" / "verify_csrf_token.py").read_text()
            assert f"except_urls: ClassVar[list[str]] = {expected}" in source

    def test_auth_routes_are_throttled_with_the_shared_limiter(self, app):
        routes = (app / "routes" / "auth.py").read_text()
        assert "from app.middleware.throttle import Throttle" in routes
        assert 'router.post("/login", login.store).middleware(ThrottleLoginAddress, ThrottleLogin)' in routes
        assert 'router.post("/register", reg.store).middleware(ThrottleRegistration)' in routes
        assert 'router.post("/forgot-password", forgot.store).middleware(ThrottlePasswordReset)' in routes
        assert 'router.post("/reset-password", reset.store).middleware(ThrottlePasswordReset)' in routes
        assert ".strip().lower()" in routes  # login key uses the lower-cased email

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({}, "CacheThrottleRequests"),
            ({"REDIS_URL": "redis://127.0.0.1:6379/0"}, "RedisThrottleRequests"),
            ({"CACHE_DRIVER": "redis"}, "RedisThrottleRequests"),
            ({"REDIS_URL": "redis://127.0.0.1:6379/0", "THROTTLE_DRIVER": "cache"}, "CacheThrottleRequests"),
        ],
    )
    def test_redis_limiter_is_selected_when_redis_is_configured(self, app, monkeypatch, env, expected):
        for key in ("REDIS_URL", "CACHE_DRIVER", "QUEUE_DRIVER", "SESSION_DRIVER", "THROTTLE_DRIVER"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location(
            "generated_throttle_under_test", app / "app" / "middleware" / "throttle.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.Throttle.__name__ == expected

    def test_admin_resources_do_not_allow_everyone(self, cwd):
        for starter in ("blog", "saas"):
            assert _new(starter, f"--starter={starter}").exit_code == 0
            for resource in (cwd / starter / "app" / "admin").glob("*_resource.py"):
                source = resource.read_text()
                assert "def can_delete" in source
                assert "return True" not in source, resource

    def test_test_fixture_and_httpx_are_provided(self, app):
        conftest = (app / "tests" / "conftest.py").read_text()
        assert "async def client" in conftest and "ASGITransport" in conftest
        dev = tomllib.loads((app / "pyproject.toml").read_text())["project"]["optional-dependencies"]["dev"]
        assert any(dep.startswith("httpx") for dep in dev)

    def test_make_test_feature_creates_missing_conftest(self, app):
        (app / "tests" / "conftest.py").unlink()
        result = CliRunner().invoke(make_test_command, ["Checkout", "--feature"])
        assert result.exit_code == 0, result.output
        assert "async def client" in (app / "tests" / "conftest.py").read_text()
        # ... and never replaces one that exists.
        (app / "tests" / "conftest.py").write_text("# mine\n")
        CliRunner().invoke(make_test_command, ["Cart", "--feature"])
        assert (app / "tests" / "conftest.py").read_text() == "# mine\n"

    def test_reset_link_is_mailed_not_left_as_a_todo(self, app):
        source = (app / "app" / "controllers" / "auth" / "password_controller.py").read_text()
        assert "TODO" not in source
        assert "Password.create_token(" in source
        assert "_send_reset_link(stored_email, token)" in source

    def test_login_controller_handles_two_factor_and_configurable_home(self, app):
        source = (app / "app" / "controllers" / "auth" / "login_controller.py").read_text()
        assert "Auth.two_factor_pending()" in source
        assert 'RedirectResponse("/two-factor/challenge")' in source
        assert 'config("auth.home", "/")' in source
        assert '"home": "/"' in (app / "config" / "auth.py").read_text()


# ---------------------------------------------------------------------------
# CLI-01: identifiers in make:*
# ---------------------------------------------------------------------------

BAD_NAMES = ["../../bootstrap/app", "a/b", "Foo.Bar", "x; import os", 'Na"me', "class", "1abc", "has space", "__"]

NAME_COMMANDS = [
    make_model_command,
    make_controller_command,
    make_migration_command,
    make_middleware_command,
    make_mail_command,
    make_notification_command,
    make_event_command,
    make_listener_command,
    make_job_command,
    make_observer_command,
    make_policy_command,
    make_request_command,
    make_resource_command,
    make_rule_command,
    make_seeder_command,
    make_factory_command,
    make_test_command,
    make_command_command,
    make_crud_command,
    make_api_command,
]


class TestIdentifierValidation:
    def test_helper(self):
        assert validate_identifier("BlogPost") == "BlogPost"
        assert validate_identifier("create_users_table") == "create_users_table"
        for bad in BAD_NAMES:
            with pytest.raises(Exception, match="not a valid"):
                validate_identifier(bad)

    @pytest.mark.parametrize("command", NAME_COMMANDS, ids=lambda c: c.name)
    @pytest.mark.parametrize("name", BAD_NAMES)
    def test_bad_names_write_nothing(self, app, command, name):
        before = _tree(app.parent)
        result = CliRunner().invoke(command, ["--", name])
        assert result.exit_code != 0, result.output
        assert _tree(app.parent) == before

    @pytest.mark.parametrize(
        "fields",
        [
            'title"); import os; os.system("id"); x=("',
            "a/b:string",
            "class:string",
            "ok:string 9bad:text",
            "x:string x:text",
            "id:integer",
        ],
    )
    @pytest.mark.parametrize("command", [make_model_command, make_crud_command, make_api_command], ids=lambda c: c.name)
    def test_bad_fields_write_nothing(self, app, command, fields):
        before = _tree(app)
        routes_before = (app / "routes" / "web.py").read_text() + (app / "routes" / "api.py").read_text()
        extra = ["-m"] if command is make_model_command else []
        result = CliRunner().invoke(command, ["Widget", "--fields", fields, *extra])
        assert result.exit_code != 0, result.output
        assert _tree(app) == before
        assert (app / "routes" / "web.py").read_text() + (app / "routes" / "api.py").read_text() == routes_before

    @pytest.mark.parametrize("option", ["--create", "--table"])
    def test_bad_table_names_write_nothing(self, app, option):
        before = _tree(app)
        result = CliRunner().invoke(make_migration_command, ["add_things", option, 'users"); import os #'])
        assert result.exit_code != 0
        assert _tree(app) == before

    def test_bad_command_signature_is_rejected(self, app):
        before = _tree(app)
        result = CliRunner().invoke(make_command_command, ["Sender", "--command", 'x")\nimport os'])
        assert result.exit_code != 0
        assert _tree(app) == before


# ---------------------------------------------------------------------------
# GEN-L2: one migration filename scheme
# ---------------------------------------------------------------------------


class TestMigrationNaming:
    def test_timestamps_are_strictly_increasing(self):
        stamps = [migration_timestamp() for _ in range(5)]
        assert stamps == sorted(set(stamps))
        assert all(re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{6}", s) for s in stamps)

    def test_mixed_generators_sort_in_creation_order(self, app):
        runner = CliRunner()
        steps = [
            (make_migration_command, ["create_alpha_table"], "create_alpha_table"),
            (session_table_command, [], "create_user_sessions_table"),
            (make_two_factor_command, [], "add_two_factor_to_users_table"),
            (make_crud_command, ["Gadget", "--fields", "name:string"], "create_gadgets_table"),
            (make_api_command, ["Widget", "--fields", "name:string"], "create_widgets_table"),
            (make_model_command, ["Zebra", "-m"], "create_zebras_table"),
        ]
        for command, args, _ in steps:
            assert runner.invoke(command, args).exit_code == 0
        names = sorted(p.stem for p in (app / "database" / "migrations").glob("*.py"))
        scaffold = [n for n in names if re.match(r"\d{4}_[a-z]", n)]
        generated = [n for n in names if n not in scaffold]
        # The scaffold's fixed prefixes run first, then the generated ones in the order they were made.
        assert names == scaffold + generated
        assert [re.sub(r"^\d{4}_\d{2}_\d{2}_\d{6}_", "", n) for n in generated] == [suffix for _, _, suffix in steps]
        assert all(re.match(r"\d{4}_\d{2}_\d{2}_\d{6}_", n) for n in generated)

    def test_session_table_is_not_generated_twice(self, app):
        runner = CliRunner()
        runner.invoke(session_table_command, [])
        second = runner.invoke(session_table_command, [])
        assert "Already exists" in second.output
        assert len(list((app / "database" / "migrations").glob("*_create_user_sessions_table.py"))) == 1

    def test_migration_stub_override_is_loaded(self, app):
        (app / "stubs").mkdir()
        (app / "stubs" / "migration.stub").write_text("# custom stub\nclass {{class}}:\n    table = '{{table}}'\n")
        assert CliRunner().invoke(make_migration_command, ["create_things_table"]).exit_code == 0
        created = next((app / "database" / "migrations").glob("*_create_things_table.py")).read_text()
        assert created.startswith("# custom stub")
        assert "class CreateThingsTable" in created and "table = 'things'" in created


# ---------------------------------------------------------------------------
# GEN-05 / GEN-07 / GEN-13 / CLI-L2 / CLI-L3 / GEN-L1 / GEN-14 (file level)
# ---------------------------------------------------------------------------


class TestGenerators:
    def test_make_api_leaves_an_existing_model_untouched(self, app):
        model = app / "app" / "models" / "user.py"
        before = model.read_bytes()
        migrations_before = sorted(p.name for p in (app / "database" / "migrations").iterdir())
        result = CliRunner().invoke(make_api_command, ["User", "--fields", "name email"])
        assert result.exit_code == 0, result.output
        assert model.read_bytes() == before
        assert "Already exists: app/models/user.py" in result.output
        # No second migration for a table that already has one.
        assert sorted(p.name for p in (app / "database" / "migrations").iterdir()) == migrations_before

    def test_make_api_supports_dry_run_and_json(self, app):
        before = _tree(app)
        result = CliRunner().invoke(make_api_command, ["Order", "--fields", "total:decimal", "--dry-run"])
        assert result.exit_code == 0 and "[dry-run]" in result.output
        assert _tree(app) == before
        result = CliRunner().invoke(make_api_command, ["Order", "--fields", "total:decimal", "--json"])
        assert '"created"' in result.output and (app / "app" / "models" / "order.py").exists()

    def test_generated_routes_require_authentication_by_default(self, app):
        runner = CliRunner()
        runner.invoke(make_crud_command, ["Article", "--fields", "title:string"])
        runner.invoke(make_api_command, ["Product", "--fields", "name:string"])
        web = (app / "routes" / "web.py").read_text()
        api = (app / "routes" / "api.py").read_text()
        for source, write_route in (
            (web, 'router.post("/articles", _c.store)'),
            (api, 'router.post("/api/products", _c.store)'),
        ):
            block = source.split("with router.group(middleware=[Authenticate]):", 1)
            assert len(block) == 2
            assert write_route in block[1]
        compile(web, "web.py", "exec")
        compile(api, "api.py", "exec")

    def test_public_flag_opts_out_explicitly(self, app):
        result = CliRunner().invoke(make_crud_command, ["Note", "--fields", "text:string", "--public"])
        assert "Warning: --public" in result.output
        web = (app / "routes" / "web.py").read_text()
        assert "Authenticate" not in web
        assert 'router.post("/notes", _c.store)' in web

    def test_make_api_uses_token_auth_in_an_api_starter_app(self, cwd, monkeypatch):
        assert _new("svc", "--starter=api").exit_code == 0
        monkeypatch.chdir(cwd / "svc")
        CliRunner().invoke(make_api_command, ["Product", "--fields", "name:string"])
        api = (cwd / "svc" / "routes" / "api.py").read_text()
        assert 'with router.group(middleware=[ApiAuth]):\n        router.get("/api/products"' in api
        compile(api, "api.py", "exec")

    def test_crud_views_work_with_the_skeleton(self, app):
        CliRunner().invoke(make_crud_command, ["Article", "--fields", "title:string body:text"])
        views = app / "resources" / "views" / "articles"
        for name in ("index.html", "show.html", "create.html", "edit.html"):
            source = (views / name).read_text()
            assert "@extends('layout')" in source
            assert "@method" not in source
        assert 'action="/articles/{{ item.id }}/update"' in (views / "edit.html").read_text()
        assert 'action="/articles/{{ item.id }}/delete"' in (views / "index.html").read_text()
        assert "values.get('title'" in (views / "edit.html").read_text()
        web = (app / "routes" / "web.py").read_text()
        assert 'router.post("/articles/{id}/update", _c.update)' in web
        assert 'router.post("/articles/{id}/delete", _c.destroy)' in web

    def test_generated_models_declare_explicit_fillable(self, app):
        runner = CliRunner()
        runner.invoke(make_model_command, ["Invoice", "--fields", "number:string total:decimal"])
        assert 'fillable: list[str] = ["number", "total"]' in (app / "app" / "models" / "invoice.py").read_text()
        result = runner.invoke(make_model_command, ["Blank"])
        assert "no --fields given" in result.output
        blank = (app / "app" / "models" / "blank.py").read_text()
        assert 'guarded: list[str] = ["*"]' in blank

    def test_stub_generated_model_rejects_unknown_keys(self, app):
        from hunt.database.model import Model

        CliRunner().invoke(make_model_command, ["Invoice", "--fields", "number:string"])
        spec = importlib.util.spec_from_file_location("generated_invoice_model", app / "app" / "models" / "invoice.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        invoice_cls = module.Invoice
        assert issubclass(invoice_cls, Model)
        instance = invoice_cls()
        try:
            instance.fill({"number": "A-1", "is_admin": True, "id": 5})
        except Exception as exc:  # a strict model may refuse the whole fill
            assert type(exc).__name__ == "MassAssignmentError"
        assert "is_admin" not in instance._attributes
        assert "id" not in instance._attributes

    @pytest.mark.parametrize(
        ("command", "args", "path"),
        [
            (make_middleware_command, ["guest"], "app/middleware/guest.py"),
            (make_mail_command, ["Welcome"], "app/mail/welcome.py"),
            (make_notification_command, ["Paid"], "app/notifications/paid.py"),
        ],
    )
    def test_existing_files_are_never_overwritten(self, app, command, args, path):
        target = app / path
        if not target.exists():
            assert CliRunner().invoke(command, args).exit_code == 0
        target.write_text("# edited by hand\n")
        result = CliRunner().invoke(command, args)
        assert result.exit_code == 0
        assert "Already exists" in result.output
        assert target.read_text() == "# edited by hand\n"

    def test_every_command_is_registered_not_only_the_first(self, app):
        runner = CliRunner()
        for name in ("SendEmails", "PruneLogs", "ThirdThing"):
            result = runner.invoke(make_command_command, [name])
            assert "Registered in: app/console/kernel.py" in result.output, result.output
        kernel = (app / "app" / "console" / "kernel.py").read_text()
        compile(kernel, "kernel.py", "exec")
        for func in ("send_emails_command", "prune_logs_command", "third_thing_command"):
            assert kernel.count(f"    cli.add_command({func})\n") == 1
            assert re.search(rf"^from app\.console\.commands\.\w+ import {func}$", kernel, re.M)

    def test_registration_failure_is_reported_not_claimed(self, app):
        (app / "app" / "console" / "kernel.py").write_text("# no register function here\n")
        result = CliRunner().invoke(make_command_command, ["Lonely"])
        assert "Registered in" not in result.output
        assert "Could not register" in result.output

    def test_policy_and_form_request_deny_by_default(self, app):
        runner = CliRunner()
        runner.invoke(make_policy_command, ["ArticlePolicy"])
        runner.invoke(make_request_command, ["StoreArticle"])
        policy = (app / "app" / "policies" / "article_policy.py").read_text()
        assert "return True" not in policy.split('"""', 2)[2]
        assert policy.count("return False") == 7
        request = (app / "app" / "requests" / "store_article_request.py").read_text()
        assert re.search(r"def authorize\(self\) -> bool:\n(\s+#.*\n)+\s+return False", request)

    def test_two_factor_scaffold_keeps_existing_files(self, app):
        runner = CliRunner()
        first = runner.invoke(make_two_factor_command, [])
        assert first.exit_code == 0, first.output
        assert (app / "resources/views/auth/two_factor/challenge.html").is_file()
        controller = app / "app" / "controllers" / "two_factor_controller.py"
        routes = app / "routes" / "two_factor.py"
        assert 'config("auth.home", "/")' in controller.read_text()
        assert "/dashboard" in controller.read_text()  # only as the framework default it replaces
        routes_source = routes.read_text()
        assert 'router.post("/two-factor/challenge", challenge.store).middleware(ThrottleTwoFactor)' in routes_source
        migration = next((app / "database" / "migrations").glob("*_add_two_factor_to_users_table.py")).read_text()
        assert 'bp.integer("two_factor_last_used_step").nullable()' in migration

        controller.write_text("# customised\n")
        routes.write_text("# customised\n")
        second = runner.invoke(make_two_factor_command, [])
        assert second.exit_code == 0
        assert controller.read_text() == "# customised\n"
        assert routes.read_text() == "# customised\n"
        assert len(list((app / "database" / "migrations").glob("*_add_two_factor_to_users_table.py"))) == 1

        forced = runner.invoke(make_two_factor_command, ["--force"])
        assert forced.exit_code == 0
        assert controller.read_text() != "# customised\n"

    def test_generated_password_column_fits_the_hash(self, app):
        from hunt.auth.manager import hash_password

        users = (app / "database" / "migrations" / "0001_create_users_table.py").read_text()
        assert 'table.string("password")' in users  # default length (255)
        assert len(hash_password("x")) <= 255


# ---------------------------------------------------------------------------
# view:cache / view:clear and session:prune
# ---------------------------------------------------------------------------


class TestViewCacheCommand:
    def test_view_cache_writes_the_files_the_loader_reads(self, app, monkeypatch):
        from hunt.console.commands.view_cache import view_cache_command, view_clear_command
        from hunt.view import factory as view_factory
        from hunt.view.factory import ViewFactory, compiled_view_cache_name

        base = app.resolve()
        views = base / "resources" / "views"
        cache = base / "storage" / "framework" / "views"
        result = CliRunner().invoke(view_cache_command, [])
        assert result.exit_code == 0, result.output

        templates = [p for p in views.rglob("*.html")]
        assert f"{len(templates)} template(s) compiled" in result.output
        expected = {
            compiled_view_cache_name(os.path.join(str(views), *t.relative_to(views).parts), ".html") for t in templates
        }
        compiled = {p.name for p in cache.iterdir() if p.name != ".gitkeep"}
        assert compiled == expected

        # The loader must serve the precompiled copy instead of compiling again.
        writes: list[str] = []
        monkeypatch.setattr(view_factory, "write_compiled_view", lambda *a, **k: writes.append(a[1]))
        welcome = compiled_view_cache_name(os.path.join(str(views), "welcome.html"), ".html")
        (cache / welcome).write_text("PRECOMPILED {{ framework }}", encoding="utf-8")
        rendered = str(ViewFactory([views], cache).make("welcome", {"framework": "hunt"}))
        assert rendered == "PRECOMPILED hunt"
        assert writes == []

        cleared = CliRunner().invoke(view_clear_command, [])
        assert cleared.exit_code == 0
        assert [p.name for p in cache.iterdir()] == [".gitkeep"]

    def test_view_cache_honours_the_configured_extension(self, app):
        from hunt.console.commands.view_cache import view_cache_command
        from hunt.view.factory import compiled_view_cache_name

        base = app.resolve()
        config = base / "config" / "view.py"
        config.write_text(config.read_text().replace('".html"', '".blade"'))
        page = base / "resources" / "views" / "page.blade"
        page.write_text("@extends('layout')\n")
        result = CliRunner().invoke(view_cache_command, [])
        assert result.exit_code == 0, result.output
        compiled = base / "storage" / "framework" / "views" / compiled_view_cache_name(str(page), ".blade")
        assert "layout.blade" in compiled.read_text()


class TestSessionPruneCommand:
    def test_registered_with_the_cli(self):
        from hunt.console.kernel import cli

        assert "session:prune" in cli.commands

    def test_prints_the_number_of_pruned_rows(self, monkeypatch):
        from hunt.console.commands import queue_work
        from hunt.console.commands.session_prune import session_prune_command
        from hunt.session.registry import SessionRegistry

        seen: list = []
        monkeypatch.setattr(queue_work, "boot_application", lambda: True)
        monkeypatch.setattr(SessionRegistry, "prune", lambda self, max_age=None: seen.append(max_age) or 3)
        result = CliRunner().invoke(session_prune_command, ["--max-age", "60"])
        assert result.exit_code == 0, result.output
        assert "Pruned 3 expired session(s)." in result.output
        assert seen == [60]

    def test_fails_outside_an_application(self, cwd, monkeypatch):
        from hunt.console.commands import queue_work
        from hunt.console.commands.session_prune import session_prune_command

        monkeypatch.setattr(queue_work, "boot_application", lambda: False)
        result = CliRunner().invoke(session_prune_command, [])
        assert result.exit_code == 1
        assert "Pruned" not in result.output

    def test_prunes_real_rows_in_a_generated_app(self, app):
        """End to end in a child process: migrate, insert two rows, prune the stale one."""
        assert CliRunner().invoke(session_table_command, []).exit_code == 0
        script = app.parent / "prune_driver.py"
        script.write_text(
            "import os, sys, time\n"
            "sys.path.insert(0, os.getcwd())\n"
            "from pathlib import Path\n"
            "import bootstrap.app\n"
            "from hunt.database.schema.migration import Migrator\n"
            "Migrator(Path.cwd() / 'database' / 'migrations').run()\n"
            "from sqlalchemy import text\n"
            "from hunt.database.connection import connection\n"
            "with connection().connect() as conn:\n"
            "    for sid, seen in (('old', 1), ('new', int(time.time()))):\n"
            '        conn.execute(text("INSERT INTO user_sessions (id, user_id, guard, last_active_at) '
            "VALUES (:i, 1, 'web', :t)\"), {'i': sid, 't': seen})\n"
            "    conn.commit()\n"
            "from click.testing import CliRunner\n"
            "from hunt.console.commands.session_prune import session_prune_command\n"
            "result = CliRunner().invoke(session_prune_command, ['--max-age', '3600'])\n"
            "print(result.output)\n"
            "with connection().connect() as conn:\n"
            "    print('LEFT', [r[0] for r in conn.execute(text('SELECT id FROM user_sessions'))])\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DB_", "DATABASE_", "APP_", "SESSION_"))}
        proc = subprocess.run(
            [sys.executable, str(script)], cwd=app, capture_output=True, text=True, timeout=120, env=env, check=False
        )
        assert proc.returncode == 0, proc.stderr[-3000:]
        assert "Pruned 1 expired session(s)." in proc.stdout
        assert "LEFT ['new']" in proc.stdout
