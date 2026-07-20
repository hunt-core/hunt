from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from pathlib import Path

import click


def _generate_app_key() -> str:
    return "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode()


_STARTERS = ("blog", "api", "saas")


@click.command("new")
@click.argument("name")
@click.option("--force", is_flag=True, help="Overwrite existing directory")
@click.option(
    "--starter",
    default=None,
    type=click.Choice(_STARTERS, case_sensitive=False),
    help="Overlay a starter kit: blog, api, or saas.",
)
def new_command(name: str, force: bool, starter: str | None) -> None:
    """Create a new hunt application skeleton."""
    target = Path.cwd() / name
    if target.exists():
        if not force:
            click.echo(f"Directory '{name}' already exists. Use --force to overwrite.", err=True)
            raise SystemExit(1)
        shutil.rmtree(target)

    dirs = [
        "app/admin",
        "app/controllers",
        "app/controllers/auth",
        "app/models",
        "app/middleware",
        "app/providers",
        "app/requests",
        "app/console",
        "app/events",
        "app/listeners",
        "app/jobs",
        "config",
        "database/migrations",
        "database/factories",
        "database/seeders",
        "resources/views/errors",
        "routes",
        "storage/app/public",
        "storage/logs",
        "storage/framework/views",
        "storage/framework/sessions",
        "storage/framework/cache",
        "bootstrap",
        "public",
        "tests",
    ]
    for d in dirs:
        (target / d).mkdir(parents=True, exist_ok=True)

    _write(target / "pyproject.toml", _PYPROJECT.replace("{{name}}", name))
    _write(target / ".gitignore", _GITIGNORE)
    _write(target / ".env.example", _ENV_EXAMPLE)
    _write_secret(  # 0o600 — owner-read only
        target / ".env",
        _ENV_EXAMPLE.replace("APP_KEY=", f"APP_KEY={_generate_app_key()}", 1),
    )
    _write(target / "config" / "app.py", _CONFIG_APP)
    _write(target / "config" / "auth.py", _CONFIG_AUTH)
    _write(target / "config" / "database.py", _CONFIG_DATABASE)
    _write(target / "config" / "session.py", _CONFIG_SESSION)
    _write(target / "config" / "view.py", _CONFIG_VIEW)
    _write(target / "config" / "mail.py", _CONFIG_MAIL)
    _write(target / "config" / "filesystems.py", _CONFIG_FILESYSTEMS)
    _write(target / "config" / "cache.py", _CONFIG_CACHE)
    _write(target / "config" / "queue.py", _CONFIG_QUEUE)
    _write(target / "config" / "logging.py", _CONFIG_LOGGING)
    _write(target / "routes" / "web.py", _ROUTES_WEB)
    _write(target / "routes" / "api.py", _ROUTES_API)
    _write(target / "routes" / "admin.py", _ROUTES_ADMIN)
    _write(target / "routes" / "auth.py", _ROUTES_AUTH)
    _write(target / "bootstrap" / "__init__.py", "")
    _write(target / "bootstrap" / "app.py", _BOOTSTRAP_APP)
    _write(target / "app" / "providers" / "app_service_provider.py", _APP_PROVIDER)
    _write(target / "app" / "providers" / "event_service_provider.py", _EVENT_PROVIDER)
    _write(target / "app" / "console" / "schedule.py", _SCHEDULE)
    _write(target / "app" / "console" / "kernel.py", _CONSOLE_KERNEL)
    _write(target / "app" / "controllers" / "welcome_controller.py", _WELCOME_CONTROLLER)
    _write(target / "app" / "controllers" / "auth" / "__init__.py", "")
    _write(target / "app" / "controllers" / "auth" / "login_controller.py", _AUTH_LOGIN_CONTROLLER)
    _write(target / "app" / "controllers" / "auth" / "register_controller.py", _AUTH_REGISTER_CONTROLLER)
    _write(target / "app" / "controllers" / "auth" / "password_controller.py", _AUTH_PASSWORD_CONTROLLER)
    _write(target / "app" / "middleware" / "guest.py", _GUEST_MIDDLEWARE)
    _write(target / "app" / "models" / "user.py", _MODEL_USER)
    _write(target / "app" / "admin" / "__init__.py", "")
    _write(target / "app" / "admin" / "user_resource.py", _ADMIN_USER_RESOURCE)
    _write(target / "database" / "seeders" / "__init__.py", "")
    _write(target / "database" / "seeders" / "DatabaseSeeder.py", _DATABASE_SEEDER)
    _write(target / "database" / "migrations" / "0001_create_users_table.py", _MIGRATION_USERS)
    _write(
        target / "database" / "migrations" / "0002_create_password_reset_tokens_table.py", _MIGRATION_PASSWORD_RESETS
    )
    _write(target / "database" / "migrations" / "0003_create_notifications_table.py", _MIGRATION_NOTIFICATIONS)
    _write(target / "database" / "migrations" / "0004_create_jobs_tables.py", _MIGRATION_JOBS)
    _write(target / "resources" / "views" / "welcome.html", _WELCOME_VIEW)
    _write(target / "resources" / "views" / "layout.html", _LAYOUT_VIEW)
    _write(target / "public" / "index.py", _PUBLIC_INDEX)
    _write(target / "tests" / "__init__.py", "")
    _write(target / "Dockerfile", _DOCKERFILE.replace("{{name}}", name))
    _write(target / ".dockerignore", _DOCKERIGNORE)

    _write_lock(target, _SCAFFOLD_FILES)

    if starter:
        _apply_starter(target, starter)

    click.echo(f"\n  Application [{name}] created successfully.\n")
    if starter:
        click.echo(f"  Starter kit applied: {starter}")
        click.echo(f"  See {name}/README.md for what was created.\n")
    click.echo("  Get started:")
    click.echo(f"    cd {name}")
    click.echo("    uv venv && uv pip install -e .")
    click.echo("    hunt migrate")
    click.echo("    hunt serve\n")


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_secret(path: Path, content: str) -> None:
    """Write a file and restrict permissions to owner-read/write only."""
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _file_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _apply_starter(target: Path, starter: str) -> None:
    """Overlay a named starter kit on top of the base skeleton."""
    if starter == "blog":
        from hunt.console.commands.starters.blog import apply
    elif starter == "api":
        from hunt.console.commands.starters.api import apply
    else:
        from hunt.console.commands.starters.saas import apply
    apply(target)


def _write_lock(root: Path, files: dict[str, str]) -> None:
    lock_dir = root / ".hunt"
    lock_dir.mkdir(exist_ok=True)
    hashes = {rel: _file_hash(content) for rel, content in files.items()}
    lock = {"version": 1, "files": hashes}
    (lock_dir / "scaffold.lock").write_text(json.dumps(lock, indent=2), encoding="utf-8")


_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{{name}}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "hunt",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.hatch.build.targets.wheel]
packages = ["app", "bootstrap", "config", "routes"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
"""

_GITIGNORE = """\
__pycache__/
*.py[cod]
.venv/
.env
storage/framework/views/
storage/logs/
database/*.sqlite
*.egg-info/
dist/
.pytest_cache/
"""

_DOCKERFILE = """\
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .

ENV APP_ENV=production \\
    APP_DEBUG=false \\
    PORT=8000

EXPOSE 8000

CMD hunt serve:production --host 0.0.0.0 --port 8000
"""

_DOCKERIGNORE = """\
.venv/
__pycache__/
*.py[cod]
.env
.git/
.pytest_cache/
storage/framework/views/
storage/logs/
database/*.sqlite
*.egg-info/
dist/
"""

_ENV_EXAMPLE = """\
APP_NAME=hunt
APP_ENV=local
APP_KEY=
APP_DEBUG=false
APP_URL=http://localhost:8000

DB_CONNECTION=sqlite
DB_DATABASE=database/database.sqlite

LOG_CHANNEL=file
LOG_LEVEL=debug

QUEUE_DRIVER=sync
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=

# MAX_BODY_SIZE=10485760

CACHE_DRIVER=file
SESSION_LIFETIME=7200
SESSION_SECURE=false

MAIL_MAILER=log
MAIL_HOST=127.0.0.1
MAIL_PORT=1025
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_ENCRYPTION=tls
MAIL_FROM_ADDRESS=hello@example.com
MAIL_FROM_NAME=hunt

FILESYSTEM_DISK=local
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
AWS_BUCKET=
AWS_URL=
"""

_CONFIG_APP = """\
import os

config = {
    "name": os.environ.get("APP_NAME", "hunt"),
    "env": os.environ.get("APP_ENV", "production"),
    "debug": os.environ.get("APP_DEBUG", "false").lower() == "true",
    "url": os.environ.get("APP_URL", "http://localhost:8000"),
    "timezone": "UTC",
    "providers": [
        "app.providers.app_service_provider.AppServiceProvider",
    ],
}
"""

_CONFIG_AUTH = """\
config = {
    "features": {
        "registration": True,
        "login": True,
        "forgot_password": True,
    }
}
"""

_CONFIG_DATABASE = """\
import os

config = {
    "default": os.environ.get("DB_CONNECTION", "sqlite"),
    "connections": {
        "sqlite": {
            "driver": "sqlite",
            "database": os.environ.get("DB_DATABASE", "database/database.sqlite"),
        },
        "mysql": {
            "driver": "mysql",
            "host": os.environ.get("DB_HOST", "127.0.0.1"),
            "port": os.environ.get("DB_PORT", "3306"),
            "database": os.environ.get("DB_DATABASE", "hunt"),
            "username": os.environ.get("DB_USERNAME", "root"),
            "password": os.environ.get("DB_PASSWORD", ""),
        },
    },
}
"""

_CONFIG_VIEW = """\
config = {
    "paths": ["resources/views"],
    "cache": "storage/framework/views",
    "extension": ".html",
}
"""

_CONFIG_MAIL = """\
import os

config = {
    "default": os.environ.get("MAIL_MAILER", "log"),
    "mailers": {
        "smtp": {
            "transport": "smtp",
            "host": os.environ.get("MAIL_HOST", "127.0.0.1"),
            "port": int(os.environ.get("MAIL_PORT", "1025")),
            "username": os.environ.get("MAIL_USERNAME"),
            "password": os.environ.get("MAIL_PASSWORD"),
            "encryption": os.environ.get("MAIL_ENCRYPTION", "tls"),
        },
        "log": {"transport": "log"},
        "array": {"transport": "array"},
    },
    "from": {
        "address": os.environ.get("MAIL_FROM_ADDRESS", "hello@example.com"),
        "name": os.environ.get("MAIL_FROM_NAME", "hunt"),
    },
}
"""

_CONFIG_SESSION = """\
import os

config = {
    # Session storage backend: "file" or "redis"
    "driver": os.environ.get("SESSION_DRIVER", "file"),
    # Session cookie lifetime in seconds (default: 7200 = 2 hours)
    "lifetime": int(os.environ.get("SESSION_LIFETIME", "7200")),
    # SameSite cookie attribute: "Strict", "Lax", or "None"
    "same_site": os.environ.get("SESSION_SAME_SITE", "Strict"),
    # Force the Secure cookie flag (always set automatically on https)
    "secure": os.environ.get("SESSION_SECURE", "").lower() == "true",
}
"""

_CONFIG_FILESYSTEMS = """\
import os
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent

config = {
    "default": os.environ.get("FILESYSTEM_DISK", "local"),
    "disks": {
        "local": {
            "driver": "local",
            "root": str(BASE_PATH / "storage" / "app"),
            "url": os.environ.get("APP_URL", "http://localhost:8000") + "/storage",
        },
        "public": {
            "driver": "local",
            "root": str(BASE_PATH / "storage" / "app" / "public"),
            "url": os.environ.get("APP_URL", "http://localhost:8000") + "/storage",
        },
        "s3": {
            "driver": "s3",
            "key": os.environ.get("AWS_ACCESS_KEY_ID", ""),
            "secret": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            "region": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            "bucket": os.environ.get("AWS_BUCKET", ""),
            "url": os.environ.get("AWS_URL", ""),
            "endpoint": os.environ.get("AWS_ENDPOINT_URL"),
        },
    },
}
"""

_CONFIG_CACHE = """\
import os

config = {
    "driver": os.environ.get("CACHE_DRIVER", "file"),
    # Used by the file driver. Relative paths resolve against the project root.
    "path": "storage/framework/cache",
    # Used by the redis driver.
    "host": os.environ.get("REDIS_HOST", "127.0.0.1"),
    "port": int(os.environ.get("REDIS_PORT", "6379")),
    "db": int(os.environ.get("REDIS_DB", "0")),
    "password": os.environ.get("REDIS_PASSWORD") or None,
    "prefix": os.environ.get("CACHE_PREFIX", "hunt_cache:"),
}
"""

_CONFIG_QUEUE = """\
import os

config = {
    "driver": os.environ.get("QUEUE_DRIVER", "sync"),
    # Used by the redis driver.
    "host": os.environ.get("REDIS_HOST", "127.0.0.1"),
    "port": int(os.environ.get("REDIS_PORT", "6379")),
    "db": int(os.environ.get("REDIS_DB", "0")),
    "password": os.environ.get("REDIS_PASSWORD") or None,
    "prefix": "hunt_queue",
}
"""

_CONFIG_LOGGING = """\
import os

config = {
    "default": os.environ.get("LOG_CHANNEL", "file"),
    "channels": {
        "file": {
            "driver": "file",
            # Relative paths resolve against the project root.
            "path": "storage/logs/hunt.log",
            "level": os.environ.get("LOG_LEVEL", "debug"),
        },
        "stderr": {
            "driver": "stderr",
            "level": os.environ.get("LOG_LEVEL", "debug"),
        },
        "stack": {
            "driver": "stack",
            "channels": ["file", "stderr"],
        },
    },
}
"""

_ROUTES_WEB = """\
from hunt.http.router import Router


def register(router: Router) -> None:
    from app.controllers.welcome_controller import WelcomeController

    router.get("/", WelcomeController().index).named("welcome")
"""

_ROUTES_API = """\
from hunt.http.router import Router


def register(router: Router) -> None:
    router.get("/api/health", lambda req: {"status": "ok"}).named("api.health")
"""

_BOOTSTRAP_APP = """\
import os
from pathlib import Path

from hunt.application import Application
from hunt.http.router import Router
from hunt.http.kernel import HttpKernel
from hunt.http.middleware.session import StartSession
from hunt.http.middleware.csrf import VerifyCsrfToken
from hunt.exceptions.handler import ExceptionHandler
from hunt.storage.manager import Storage
from hunt.support.helpers import _set_app

BASE_PATH = Path(__file__).resolve().parent.parent

# Constructing the Application loads .env and the config/ directory, and
# configures the log, cache, queue, mail and storage managers from it.
application = Application(BASE_PATH)
_set_app(application)

# -- Service providers
from app.providers.app_service_provider import AppServiceProvider
from app.providers.event_service_provider import AppEventServiceProvider
application.bootstrap([AppServiceProvider, AppEventServiceProvider])

# -- Router
router = Router()
application.instance("router", router)

# -- Auth model
from app.models.user import User as _User
from hunt.auth.manager import Auth as _Auth
_Auth.set_model(_User)

# -- Load routes
from routes.web import register as web_routes
from routes.api import register as api_routes
from routes.auth import register as auth_routes
from routes.admin import register as admin_routes
web_routes(router)
api_routes(router)
auth_routes(router)
admin_routes(router)

# -- Register named routes
for route in router.routes():
    if route.name:
        router._named[route.name] = route

# -- View factory (configured from config/view.py at boot)
view_factory = application.make("view")

def _storage_url(path: str | None) -> str:
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    try:
        return Storage.disk("public").url(path)
    except Exception:
        return path

def _route(name: str, **params) -> str:
    try:
        return router.url(name, params or None)
    except Exception:
        return ""

_app_url = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")

def _asset(path: str) -> str:
    return _app_url + "/" + path.lstrip("/")

def _auth_user():
    from hunt.auth.manager import Auth
    return Auth.user()

def _auth_check() -> bool:
    from hunt.auth.manager import Auth
    return Auth.check()

def _config(key: str, default=None):
    return application.config.get(key, default)

view_factory.share("storage_url", _storage_url)
view_factory.share("route", _route)
view_factory.share("asset", _asset)
view_factory.share("auth_user", _auth_user)
view_factory.share("auth_check", _auth_check)
view_factory.share("config", _config)

# -- Exception handler
debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
exc_handler = ExceptionHandler(debug=debug, views_path=BASE_PATH / "resources" / "views")

# -- Global middleware
global_middleware = [
    StartSession,
    VerifyCsrfToken,
]

# -- HTTP Kernel (ASGI app)
kernel = HttpKernel(router, global_middleware=global_middleware, exception_handler=exc_handler, app=application)
application.instance("kernel", kernel)

# ASGI entry point
async def app(scope, receive, send):
    await kernel(scope, receive, send)
"""

_APP_PROVIDER = """\
from hunt.container.provider import ServiceProvider


class AppServiceProvider(ServiceProvider):
    def register(self) -> None:
        pass

    def boot(self) -> None:
        pass
"""

_EVENT_PROVIDER = """\
from hunt.events.provider import EventServiceProvider

# from app.events.UserRegistered import UserRegistered
# from app.listeners.SendWelcomeEmail import SendWelcomeEmail


class AppEventServiceProvider(EventServiceProvider):
    listen: dict = {
        # UserRegistered: [
        #     SendWelcomeEmail,
        # ],
    }
"""

_SCHEDULE = """\
from hunt.scheduling import Scheduler


def schedule(scheduler: Scheduler) -> None:
    pass
    # scheduler.call(lambda: None, description="Example task").daily()
    # scheduler.command("db:seed").weekly()
"""

_CONSOLE_KERNEL = """\
from __future__ import annotations

import click


def register(cli: click.Group) -> None:
    \"\"\"Register application commands with the hunt CLI.\"\"\"
    pass
    # from app.console.commands.my_command import my_command
    # cli.add_command(my_command)
"""

_WELCOME_CONTROLLER = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response


class WelcomeController(Controller):
    def index(self, request: Request) -> Response:
        return self.view("welcome", {"framework": "hunt"})
"""

_WELCOME_VIEW = """\
@extends('layout')

@section('content')
<div class="hero">
    <h1>Welcome to <span>{{ framework }}</span></h1>
    <p>Your hunt application is up and running.</p>
    <a href="https://hunt-framework.com">Documentation</a>
</div>
@endsection
"""

_LAYOUT_VIEW = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ config('app.name', 'hunt') if config is defined else 'hunt' }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, sans-serif; background: #0f0f0f; color: #e5e5e5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .hero { text-align: center; }
        .hero h1 { font-size: 3rem; font-weight: 700; letter-spacing: -1px; }
        .hero h1 span { color: #6366f1; }
        .hero p { margin: 1rem 0 2rem; color: #999; font-size: 1.1rem; }
        .hero a { padding: .6rem 1.4rem; background: #6366f1; color: #fff; border-radius: 6px; text-decoration: none; font-weight: 500; }
    </style>
</head>
<body>
    {% block content %}{% endblock %}
</body>
</html>
"""

_PUBLIC_INDEX = """\
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bootstrap.app import app  # noqa: F401 — ASGI entry point
"""

_DATABASE_SEEDER = """\
from hunt.database.seeder import Seeder


class DatabaseSeeder(Seeder):
    def run(self) -> None:
        # self.call(UserSeeder)
        pass
"""

_MIGRATION_USERS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateUsersTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.id()
            table.string("name")
            table.string("email").unique()
            table.string("password")
            table.boolean("is_admin").default(False)
            table.timestamp("email_verified_at").nullable()
            table.string("remember_token", 100).nullable()
            table.timestamps()

        Schema.create("users", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("users")
"""

_MIGRATION_PASSWORD_RESETS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreatePasswordResetTokensTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.string("email")
            table.string("token")
            table.timestamp("created_at").nullable()
            table.index("email")

        Schema.create("password_reset_tokens", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("password_reset_tokens")
"""

_MIGRATION_NOTIFICATIONS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateNotificationsTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.string("id", 36)
            table.string("type")
            table.integer("notifiable_id")
            table.string("notifiable_type")
            table.text("data")
            table.timestamp("read_at").nullable()
            table.timestamps()
            table.index(["notifiable_id", "notifiable_type"])

        Schema.create("notifications", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("notifications")
"""

_MIGRATION_JOBS = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateJobsTables(Migration):
    def up(self) -> None:
        def jobs_blueprint(table):
            table.id()
            table.string("queue", 255).default("default")
            table.text("payload")
            table.small_integer("attempts").default(0)
            table.integer("reserved_at").nullable()
            table.integer("available_at").nullable()
            table.integer("created_at")
            table.index("queue")

        Schema.create("jobs", jobs_blueprint)

        def failed_blueprint(table):
            table.id()
            table.string("uuid", 36)
            table.string("connection", 255)
            table.string("queue", 255)
            table.text("payload")
            table.text("exception")
            table.integer("failed_at")
            table.unique("uuid")

        Schema.create("jobs_failed", failed_blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("jobs_failed")
        Schema.drop_if_exists("jobs")
"""

_MODEL_USER = """\
from hunt.database.model import Model


class User(Model):
    table = "users"
    fillable = ["name", "email", "password"]
    hidden = ["password", "remember_token"]
    casts = {
        "is_admin": "boolean",
    }
"""

_ADMIN_USER_RESOURCE = """\
from hunt.admin import AdminResource
from hunt.admin.fields import Text, Email, Password, Boolean, DateTime
from app.models.user import User


class UserResource(AdminResource):
    model = User
    label = "User"
    search_columns = ["name", "email"]
    default_order = ("created_at", "desc")
    per_page = 20

    def fields(self):
        return [
            Text("Id", attribute="id").readonly().sortable(),
            Text("Name", attribute="name").rules("required", "string", "max:255").sortable(),
            Email("Email", attribute="email").rules("required", "email").sortable(),
            Password("Password", attribute="password").rules("required", "min:8").hide_from_edit(),
            Boolean("Admin", attribute="is_admin"),
            DateTime("Created At", attribute="created_at").sortable().hide_from_forms(),
        ]

    def can_view_any(self, request) -> bool:
        return True

    def can_create(self, request) -> bool:
        return True

    def can_update(self, request, instance=None) -> bool:
        return True

    def can_delete(self, request, instance=None) -> bool:
        return True
"""

_GUEST_MIDDLEWARE = """\
from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse


class RedirectIfAuthenticated(Middleware):
    \"\"\"Redirect already-authenticated users away from guest-only pages.\"\"\"

    redirect_to: str = "/"

    async def handle(self, request: Request, next: Next) -> Response:
        from hunt.auth.manager import Auth

        if Auth.check():
            return RedirectResponse(self.redirect_to)
        return await next(request)
"""

_AUTH_LOGIN_CONTROLLER = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.auth.manager import Auth
from hunt.validation.validator import Validator


class LoginController(Controller):
    def show(self, request: Request) -> Response:
        status = "Your password has been reset." if request.query("reset") else None
        return self.view("auth.login", {"errors": {}, "old": {}, "status": status})

    def store(self, request: Request) -> Response:
        data = {
            "email": request.input("email", ""),
            "password": request.input("password", ""),
        }
        validator = Validator.make(data, {"email": "required|email", "password": "required"})
        if validator.fails():
            return self.view("auth.login", {
                "errors": validator.errors()._errors,
                "old": data,
                "status": None,
            })

        if not Auth.attempt({"email": data["email"], "password": data["password"]}):
            return self.view("auth.login", {
                "errors": {"email": ["These credentials do not match our records."]},
                "old": data,
                "status": None,
            })

        return RedirectResponse("/")

    def destroy(self, request: Request) -> Response:
        Auth.logout()
        return RedirectResponse("/login")
"""

_AUTH_REGISTER_CONTROLLER = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.auth.manager import Auth, hash_password
from hunt.validation.validator import Validator
from app.models.user import User


class RegisterController(Controller):
    def show(self, request: Request) -> Response:
        return self.view("auth.register", {"errors": {}, "old": {}})

    def store(self, request: Request) -> Response:
        data = {
            "name": request.input("name", ""),
            "email": request.input("email", ""),
            "password": request.input("password", ""),
            "password_confirmation": request.input("password_confirmation", ""),
        }
        validator = Validator.make(data, {
            "name": "required|string|max:255",
            "email": "required|email|unique:users,email",
            "password": "required|min:8|confirmed",
        })
        if validator.fails():
            return self.view("auth.register", {
                "errors": validator.errors()._errors,
                "old": {k: v for k, v in data.items() if k != "password"},
            })

        user = User.create({
            "name": data["name"],
            "email": data["email"],
            "password": hash_password(data["password"]),
        })
        Auth.login(user)
        return RedirectResponse("/")
"""

_AUTH_PASSWORD_CONTROLLER = """\
import hashlib
import secrets
import time

from sqlalchemy import text

from hunt.database.connection import connection
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.auth.manager import hash_password
from hunt.validation.validator import Validator
from app.models.user import User

_TOKEN_TTL = 3600  # 60 minutes


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ForgotPasswordController(Controller):
    def show(self, request: Request) -> Response:
        return self.view("auth.forgot_password", {"errors": {}, "status": None})

    def store(self, request: Request) -> Response:
        data = {"email": request.input("email", "")}
        validator = Validator.make(data, {"email": "required|email"})
        if validator.fails():
            return self.view("auth.forgot_password", {
                "errors": validator.errors()._errors,
                "status": None,
            })

        user = User.where("email", data["email"]).first()
        # Always hit the DB so response time doesn't reveal whether the email exists
        engine = connection()
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM password_reset_tokens WHERE email = :e"),
                {"e": data["email"]},
            )
            if user:
                token = secrets.token_urlsafe(32)
                conn.execute(
                    text("INSERT INTO password_reset_tokens (email, token, created_at) VALUES (:e, :t, :c)"),
                    {"e": data["email"], "t": _hash_token(token), "c": int(time.time())},
                )
                # TODO: email the reset link — /reset-password/{token}?email={email}
            conn.commit()

        return self.view("auth.forgot_password", {
            "errors": {},
            "status": "If that email address is registered you will receive a reset link shortly.",
        })


class ResetPasswordController(Controller):
    def show(self, request: Request, token: str) -> Response:
        return self.view("auth.reset_password", {
            "token": token,
            "email": request.query("email", ""),
            "errors": {},
        })

    def store(self, request: Request) -> Response:
        data = {
            "email": request.input("email", ""),
            "token": request.input("token", ""),
            "password": request.input("password", ""),
            "password_confirmation": request.input("password_confirmation", ""),
        }
        validator = Validator.make(data, {
            "email": "required|email",
            "token": "required",
            "password": "required|min:8|confirmed",
        })
        if validator.fails():
            return self.view("auth.reset_password", {
                "token": data["token"],
                "email": data["email"],
                "errors": validator.errors()._errors,
            })

        engine = connection()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT email, token, created_at FROM password_reset_tokens WHERE email = :e AND token = :t"),
                {"e": data["email"], "t": _hash_token(data["token"])},
            ).fetchone()

        invalid_ctx = {"token": data["token"], "email": data["email"],
                       "errors": {"email": ["This password reset link is invalid or has expired."]}}

        if not row:
            return self.view("auth.reset_password", invalid_ctx)
        if (int(time.time()) - (row[2] or 0)) > _TOKEN_TTL:
            return self.view("auth.reset_password", invalid_ctx)

        user = User.where("email", data["email"]).first()
        if user is None:
            return self.view("auth.reset_password", invalid_ctx)

        user._attributes["password"] = hash_password(data["password"])
        user.save()

        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM password_reset_tokens WHERE email = :e"),
                {"e": data["email"]},
            )
            conn.commit()

        return RedirectResponse("/login?reset=1")
"""

_ROUTES_AUTH = """\
from hunt.http.middleware.throttle import ThrottleRequests
from hunt.http.router import Router
from hunt.support.helpers import config
from app.controllers.auth.login_controller import LoginController
from app.controllers.auth.register_controller import RegisterController
from app.controllers.auth.password_controller import ForgotPasswordController, ResetPasswordController
from app.middleware.guest import RedirectIfAuthenticated


class ThrottleLogin(ThrottleRequests):
    max_attempts = 5
    decay_seconds = 60


def register(router: Router) -> None:
    features = config("auth.features", {})
    login_enabled = features.get("login", True)
    registration_enabled = features.get("registration", True)
    forgot_enabled = features.get("forgot_password", True)

    login = LoginController()

    with router.group(middleware=[RedirectIfAuthenticated]):
        if login_enabled:
            router.get("/login", login.show).named("login")
            router.post("/login", login.store).middleware(ThrottleLogin)
        if registration_enabled:
            reg = RegisterController()
            router.get("/register", reg.show).named("register")
            router.post("/register", reg.store)
        if forgot_enabled:
            forgot = ForgotPasswordController()
            router.get("/forgot-password", forgot.show).named("password.request")
            router.post("/forgot-password", forgot.store).named("password.email")

    if login_enabled:
        router.post("/logout", login.destroy).named("logout")
    if forgot_enabled:
        reset = ResetPasswordController()
        router.get("/reset-password/{token}", reset.show).named("password.reset")
        router.post("/reset-password", reset.store).named("password.update")
"""

_ROUTES_ADMIN = """\
from hunt.admin import Admin
from hunt.admin.metrics import ValueMetric
from hunt.http.router import Router
from app.admin.user_resource import UserResource
from app.models.user import User

Admin.resource(UserResource)

Admin.dashboard(
    ValueMetric("Total Users", lambda: User.query().count()),
)


def register(router: Router) -> None:
    from hunt.auth.manager import Auth

    Admin.gate(
        lambda request: Auth.check()
        and bool(getattr(Auth.user(), "_attributes", {}).get("is_admin"))
    )
    Admin.register_to(router)
"""

# Config files written by `hunt new` and added (never overwritten) by `hunt upgrade`.
_NEW_CONFIG_FILES: dict[str, str] = {
    "config/app.py": _CONFIG_APP,
    "config/auth.py": _CONFIG_AUTH,
    "config/database.py": _CONFIG_DATABASE,
    "config/session.py": _CONFIG_SESSION,
    "config/view.py": _CONFIG_VIEW,
    "config/mail.py": _CONFIG_MAIL,
    "config/filesystems.py": _CONFIG_FILESYSTEMS,
    "config/cache.py": _CONFIG_CACHE,
    "config/queue.py": _CONFIG_QUEUE,
    "config/logging.py": _CONFIG_LOGGING,
}

# Canonical set of scaffold-managed files written by `hunt new`.
# Keys are relative paths; values are the expected initial content.
# Used by `hunt upgrade` for hash-based safe-update decisions.
_SCAFFOLD_FILES: dict[str, str] = {
    "database/migrations/0001_create_users_table.py": _MIGRATION_USERS,
    "database/migrations/0002_create_password_reset_tokens_table.py": _MIGRATION_PASSWORD_RESETS,
    "app/models/user.py": _MODEL_USER,
    "app/controllers/auth/__init__.py": "",
    "app/controllers/auth/login_controller.py": _AUTH_LOGIN_CONTROLLER,
    "app/controllers/auth/register_controller.py": _AUTH_REGISTER_CONTROLLER,
    "app/controllers/auth/password_controller.py": _AUTH_PASSWORD_CONTROLLER,
    "app/middleware/guest.py": _GUEST_MIDDLEWARE,
    "app/admin/__init__.py": "",
    "app/admin/user_resource.py": _ADMIN_USER_RESOURCE,
    "app/console/kernel.py": _CONSOLE_KERNEL,
    "routes/auth.py": _ROUTES_AUTH,
    "routes/admin.py": _ROUTES_ADMIN,
}
