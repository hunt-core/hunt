from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import click


def _generate_app_key() -> str:
    return "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode()


#: Name this framework is published under on PyPI. The bare name "hunt" is an unrelated project.
FRAMEWORK_DISTRIBUTION = "hunt-framework"


def framework_requirement(version: str | None = None) -> str:
    """Return the PEP 508 requirement generated apps use for the framework.

    The range is pinned to the running minor series: ``hunt-framework>=X.Y,<X.(Y+1)``.
    """
    if version is None:
        from hunt import __version__ as version
    parts = str(version).split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return FRAMEWORK_DISTRIBUTION
    return f"{FRAMEWORK_DISTRIBUTION}>={major}.{minor},<{major}.{minor + 1}"


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
    target = _resolve_target(name)
    if target.is_symlink() or target.exists():
        if not force:
            click.echo(f"Directory '{name}' already exists. Use --force to overwrite.", err=True)
            raise SystemExit(1)
        _remove_existing(target, name)

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

    # Runtime state is git-ignored; these keep the directories in a fresh clone.
    for d in _GITKEEP_DIRS:
        _write(target / d / ".gitkeep", "")

    _write(
        target / "pyproject.toml",
        _PYPROJECT.replace("{{name}}", name).replace("{{framework_requirement}}", framework_requirement()),
    )
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
    _write(target / "app" / "middleware" / "__init__.py", "")
    _write(target / "app" / "middleware" / "guest.py", _GUEST_MIDDLEWARE)
    _write(target / "app" / "middleware" / "throttle.py", _THROTTLE_MIDDLEWARE)
    _write(target / "app" / "middleware" / "verify_csrf_token.py", csrf_middleware_source([]))
    _write(target / "app" / "middleware" / "secure_headers.py", secure_headers_source())
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
    from hunt.console.commands.queue_table import _PROCESSED_MIGRATION_STUB

    _write(target / "database" / "migrations" / "0005_create_jobs_processed_table.py", _PROCESSED_MIGRATION_STUB)
    _write(target / "resources" / "views" / "welcome.html", _WELCOME_VIEW)
    _write(target / "resources" / "views" / "layout.html", _LAYOUT_VIEW)
    _write(target / "public" / "index.py", _PUBLIC_INDEX)
    _write(target / "tests" / "__init__.py", "")
    from hunt.console.commands.make.test import CONFTEST_STUB

    _write(target / "tests" / "conftest.py", CONFTEST_STUB)
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
    """Create a file that is owner-read/write only from the moment it exists."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        # An already existing file keeps its old mode on open(); tighten it before writing.
        if hasattr(os, "fchmod"):
            os.fchmod(fh.fileno(), 0o600)
        fh.write(content)
    if not hasattr(os, "fchmod"):
        path.chmod(0o600)


_APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Marker written into every scaffold; `hunt new --force` only deletes directories that contain it
#: without asking.
SCAFFOLD_MARKER = Path(".hunt") / "scaffold.lock"


def _resolve_target(name: str) -> Path:
    """Validate the application name and return the directory to create.

    The name must be a single path component directly below the working
    directory: no separators, no ``.``/``..``, no absolute paths.
    """
    if not _APP_NAME_RE.match(name) or name in {".", ".."} or name.endswith("."):
        raise click.BadParameter(
            f"{name!r} is not a valid application name. Use a single directory name made of letters, "
            "digits, '.', '_' and '-' (starting with a letter or digit).",
            param_hint="NAME",
        )
    cwd = Path.cwd().resolve()
    target = cwd / name
    if target.parent != cwd or (not target.is_symlink() and target.resolve().parent != cwd):
        raise click.BadParameter("The application must be created directly inside the current directory.")
    return target


def _remove_existing(target: Path, name: str) -> None:
    """Delete a previous scaffold for --force; anything else needs explicit confirmation."""
    if target.is_symlink() or not target.is_dir():
        click.echo(f"'{name}' exists and is not a directory created by `hunt new`; refusing to delete it.", err=True)
        raise SystemExit(1)
    if not (target / SCAFFOLD_MARKER).is_file():
        click.echo(
            f"'{name}' does not look like a hunt application (no {SCAFFOLD_MARKER.as_posix()} marker).",
            err=True,
        )
        confirmed = False
        try:
            confirmed = click.confirm(f"  Permanently delete '{name}' and everything in it?", default=False, err=True)
        except click.Abort:
            confirmed = False
        if not confirmed:
            click.echo("  Aborted. Nothing was deleted.", err=True)
            raise SystemExit(1)
    shutil.rmtree(target)


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
    "{{framework_requirement}}",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
]

[tool.hatch.build.targets.wheel]
packages = ["app", "bootstrap", "config", "routes"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
"""

# Directories whose contents are runtime state. Each gets a .gitkeep.
_GITKEEP_DIRS = (
    "storage/app",
    "storage/app/public",
    "storage/logs",
    "storage/framework/views",
    "storage/framework/sessions",
    "storage/framework/cache",
)

# Runtime state that must never be committed or baked into an image: live
# sessions, the cache, the config cache (resolved secrets), uploads and the
# maintenance sentinel.
_RUNTIME_IGNORES = """\
storage/framework/views/*
storage/framework/sessions/*
storage/framework/cache/*
storage/framework/config.json
storage/logs/*
storage/app/*
!storage/app/public/
storage/app/public/*
!storage/**/.gitkeep
.maintenance
"""

_GITIGNORE = (
    """\
__pycache__/
*.py[cod]
.venv/
.env
.env.*
!.env.example
"""
    + _RUNTIME_IGNORES
    + """\
database/*.sqlite
database/*.sqlite-*
*.egg-info/
dist/
.pytest_cache/
"""
)

_DOCKERFILE = """\
# Base image pinned by version and digest. Bump both together (Dependabot and
# Renovate can do this for you) to pick up security fixes.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# -- Build stage: the compiler toolchain stays here ---------------------------
FROM ${PYTHON_IMAGE} AS build

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml .
# Install the dependencies declared in pyproject.toml; the application itself runs from /app.
RUN python -c "import subprocess, sys, tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *deps])"

# -- Runtime stage -------------------------------------------------------------
FROM ${PYTHON_IMAGE}

ENV PATH="/opt/venv/bin:$PATH" \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    APP_ENV=production \\
    APP_DEBUG=false \\
    PORT=8000

RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY . .

# Only the directories the application writes to belong to the runtime user.
# `database` is included for the default SQLite file; mount a volume there (or
# use a database server) to keep data across containers.
RUN mkdir -p storage/app/public storage/logs storage/framework/views \\
        storage/framework/sessions storage/framework/cache database \\
    && chown -R app:app storage \\
    && chown app:app database

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"]

# Behind a reverse proxy add: "--forwarded-allow-ips", "<proxy address>"
CMD ["hunt", "serve:production", "--host", "0.0.0.0", "--port", "8000"]
"""

_DOCKERIGNORE = (
    """\
.venv/
__pycache__/
*.py[cod]
.env
.env.*
.git/
.hunt/
.pytest_cache/
"""
    + _RUNTIME_IGNORES
    + """\
database/*.sqlite
database/*.sqlite-*
*.egg-info/
dist/
"""
)

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
# The session cookie is Secure when APP_ENV=production (and on any https
# request). Set SESSION_SECURE=true or false to force it either way.
# SESSION_SECURE=

MAIL_MAILER=log
# The "log" mailer writes message bodies (e.g. reset links) to the log when
# APP_ENV=local. Set MAIL_LOG_BODY=true/false to force it either way.
# MAIL_LOG_BODY=
MAIL_HOST=127.0.0.1
MAIL_PORT=1025
MAIL_USERNAME=
MAIL_PASSWORD=
# none (local Mailpit on port 1025), tls (STARTTLS, port 587) or ssl (port 465).
MAIL_ENCRYPTION=none
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
    # Where users land after logging in, registering or passing a 2FA challenge.
    "home": "/",
    "features": {
        "registration": True,
        "login": True,
        # The reset link is sent through the mailer in config/mail.py. The
        # default "log" mailer does not deliver email: configure SMTP in .env
        # (with APP_ENV=local the link is written to the log instead).
        "forgot_password": True,
    },
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
        "pgsql": {
            "driver": "pgsql",
            "host": os.environ.get("DB_HOST", "127.0.0.1"),
            "port": os.environ.get("DB_PORT", "5432"),
            "database": os.environ.get("DB_DATABASE", "hunt"),
            "username": os.environ.get("DB_USERNAME", "postgres"),
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
            # "tls" (STARTTLS), "ssl" or "none". Unset or empty falls back to tls.
            "encryption": os.environ.get("MAIL_ENCRYPTION") or "tls",
        },
        # Message bodies (which can contain password reset links) are written
        # to the log only when APP_ENV=local, unless MAIL_LOG_BODY=true/false
        # forces it. Never enable it in production.
        "log": {
            "transport": "log",
            "log_body": {"true": True, "false": False}.get(os.environ.get("MAIL_LOG_BODY", "").strip().lower()),
        },
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
    # Secure cookie flag. Left unset (None) the framework decides: on when
    # APP_ENV is production, and always on for https requests. Set
    # SESSION_SECURE=true or false in .env to force it either way.
    "secure": {"true": True, "false": False}.get(os.environ.get("SESSION_SECURE", "").strip().lower()),
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
    # Seconds a job may stay reserved before another worker takes it over.
    # Must be longer than your longest job timeout.
    "retry_after": int(os.environ.get("QUEUE_RETRY_AFTER", "90")),
    # Seconds the ids of finished jobs are remembered so the same job is never
    # run twice (and after which an undelivered job is treated as stale).
    "processed_retention": int(os.environ.get("QUEUE_PROCESSED_RETENTION", str(7 * 24 * 60 * 60))),
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
from hunt.http.middleware.maintenance import MaintenanceMode
from hunt.http.middleware.session import StartSession
from hunt.http.middleware.trust_proxies import TrustProxies  # noqa: F401 - see global_middleware
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

# -- Global middleware (outermost first)
from app.middleware.secure_headers import SecureHeaders
from app.middleware.verify_csrf_token import VerifyCsrfToken

global_middleware = [
    # Answers 503 while `hunt down` is active; `hunt up` restores service.
    MaintenanceMode,
    # Behind a reverse proxy or load balancer, enable TrustProxies and set
    # TRUSTED_PROXIES to the proxy addresses. Without it every client shares
    # the proxy's IP address, which breaks rate limiting and logging.
    # TrustProxies,
    SecureHeaders,
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
    # Only these columns can be mass assigned (create()/fill()). Privileged
    # columns such as is_admin must be set explicitly: user.is_admin = True.
    # Always store passwords with hunt.auth.manager.hash_password().
    fillable = ["name", "email", "password"]
    hidden = ["password", "remember_token"]
    casts = {
        "is_admin": "boolean",
    }

    def save(self) -> bool:
        # An administrator cannot remove their own admin flag (for example by
        # unticking "Admin" on their own record in the admin panel), which
        # could leave the application without any administrator.
        if self._exists and self._original.get("is_admin") and not self._attributes.get("is_admin"):
            from hunt.auth.manager import Auth

            if Auth.id() is not None and str(Auth.id()) == str(self._attributes.get("id")):
                self._attributes["is_admin"] = self._original["is_admin"]
        return super().save()
"""

_ADMIN_USER_RESOURCE = """\
from hunt.admin import AdminResource
from hunt.admin.fields import Text, Email, Password, Boolean, DateTime
from hunt.auth.manager import Auth
from app.models.user import User


def is_admin(request) -> bool:
    \"\"\"True when the current user is an administrator.\"\"\"
    user = Auth.user()
    return bool(user is not None and user._attributes.get("is_admin"))


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
            # The admin panel hashes Password fields before they are stored.
            Password("Password", attribute="password").rules("required", "min:8").hide_from_edit(),
            # is_admin is not in User.fillable; the admin panel saves the fields
            # declared here explicitly, so only administrators can change it.
            # User.save() refuses to let an administrator demote themselves.
            Boolean("Admin", attribute="is_admin"),
            DateTime("Created At", attribute="created_at").sortable().hide_from_forms(),
        ]

    # Each ability is limited to administrators. Narrow these further (or
    # return False) for resources that should be read-only in the panel.
    def can_view_any(self, request) -> bool:
        return is_admin(request)

    def can_create(self, request) -> bool:
        return is_admin(request)

    def can_update(self, request, instance=None) -> bool:
        return is_admin(request)

    def can_delete(self, request, instance=None) -> bool:
        # Administrators cannot delete their own account from the panel.
        if instance is not None and getattr(instance, "_attributes", {}).get("id") == Auth.id():
            return False
        return is_admin(request)
"""

_CSRF_MIDDLEWARE = """\
from typing import ClassVar

from hunt.http.middleware.csrf import VerifyCsrfToken as Middleware


class VerifyCsrfToken(Middleware):
    \"\"\"CSRF protection for every state-changing request.

    List URL prefixes here ONLY when the routes below them do not rely on the
    session cookie for authentication (token-authenticated APIs, signed
    webhooks). Exempting a cookie-authenticated route makes it forgeable.
    \"\"\"

    except_urls: ClassVar[list[str]] = {{except_urls}}
"""


def csrf_middleware_source(except_urls: list[str]) -> str:
    """Render app/middleware/verify_csrf_token.py with the given exempt URL prefixes."""
    return _CSRF_MIDDLEWARE.replace("{{except_urls}}", json.dumps(list(except_urls)))


# The scaffold's own pages use inline <style> and <script> blocks, so the
# default policy has to allow those. No third-party script host is needed:
# the 2FA setup QR code is rendered on the server.
BASE_SCRIPT_SOURCES = "'self' 'unsafe-inline'"

_SECURE_HEADERS_MIDDLEWARE = """\
import os

from hunt.http.middleware.secure_headers import SecureHeaders as Middleware
from hunt.http.response import Response


class SecureHeaders(Middleware):
    \"\"\"Security headers for every response (clickjacking, MIME sniffing, HSTS, CSP).

    The Content-Security-Policy below allows what the generated pages use.
    Tighten it as you remove inline scripts and styles, or override it per
    environment with SECURE_CONTENT_SECURITY_POLICY. The admin panel sends
    its own policy.
    \"\"\"

    content_security_policy = (
        "default-src 'self'; "
        "script-src {{script_src}}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    def _apply(self, response: Response) -> None:
        if (
            "SECURE_CONTENT_SECURITY_POLICY" not in os.environ
            and response.get_header("Content-Security-Policy") is None
        ):
            response.header("Content-Security-Policy", self.content_security_policy)
        super()._apply(response)
"""


def secure_headers_source(extra_script_sources: str = "") -> str:
    """Render app/middleware/secure_headers.py; starters add the CDNs their layouts load."""
    sources = BASE_SCRIPT_SOURCES + (f" {extra_script_sources}" if extra_script_sources else "")
    return _SECURE_HEADERS_MIDDLEWARE.replace("{{script_src}}", sources)


_THROTTLE_MIDDLEWARE = """\
\"\"\"Rate limiting that is shared by every server worker.

`hunt serve:production` starts several worker processes. The framework's
in-memory `ThrottleRequests` counts per process, so a limit of 5 would really
be 5 x workers. The limiters here keep their counters in a shared store:

* Redis (sliding window) when Redis is configured, otherwise
* the application cache from config/cache.py (fixed window). The default
  file cache is shared by all workers on one machine; use Redis when you run
  more than one machine.
\"\"\"
import hashlib
import os
import time

from hunt.cache.manager import Cache
from hunt.http.middleware import Middleware, Next
from hunt.http.middleware.throttle import RedisThrottleRequests
from hunt.http.request import Request
from hunt.http.response import HttpException, Response


def redis_configured() -> bool:
    \"\"\"True when the environment points the app at a Redis server.\"\"\"
    if os.environ.get("THROTTLE_DRIVER"):
        return os.environ["THROTTLE_DRIVER"].lower() == "redis"
    if os.environ.get("REDIS_URL"):
        return True
    drivers = {os.environ.get(name, "").lower() for name in ("CACHE_DRIVER", "QUEUE_DRIVER", "SESSION_DRIVER")}
    return "redis" in drivers


class CacheThrottleRequests(Middleware):
    \"\"\"Fixed-window rate limiter backed by the application cache.\"\"\"

    max_attempts: int = 60
    decay_seconds: int = 60

    async def handle(self, request: Request, next: Next) -> Response:
        now = time.time()
        window = int(now // self.decay_seconds)
        digest = hashlib.sha256(self._resolve_key(request).encode()).hexdigest()
        key = f"throttle:{digest}:{window}"

        # add() and increment() are atomic, so concurrent workers cannot lose a hit.
        Cache.add(key, 0, self.decay_seconds)
        hits = int(Cache.increment(key))
        if hits > self.max_attempts:
            retry_after = max(1, int((window + 1) * self.decay_seconds - now))
            raise HttpException(
                429,
                f"Too many requests. Retry after {retry_after}s.",
                {"Retry-After": str(retry_after)},
            )

        response = await next(request)
        response.header("X-RateLimit-Limit", str(self.max_attempts))
        response.header("X-RateLimit-Remaining", str(max(0, self.max_attempts - hits)))
        return response

    def _resolve_key(self, request: Request) -> str:
        route = getattr(request, "route", None)
        target = getattr(route, "uri", None) or request.path
        return f"{request.ip}:{request.method}:{target}"


# Base class for the application's rate limiters.
Throttle = RedisThrottleRequests if redis_configured() else CacheThrottleRequests
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
from hunt.support.helpers import config
from hunt.validation.validator import Validator


def home_path() -> str:
    \"\"\"Where to send users after login. Change "home" in config/auth.py.\"\"\"
    return str(config("auth.home", "/") or "/")


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
                "old": {"email": data["email"]},
                "status": None,
            })

        if not Auth.attempt({"email": data["email"], "password": data["password"]}):
            if Auth.two_factor_pending():
                # The password was correct but the account uses two-factor
                # authentication: finish the login on the challenge page.
                return RedirectResponse("/two-factor/challenge")
            return self.view("auth.login", {
                "errors": {"email": ["These credentials do not match our records."]},
                "old": {"email": data["email"]},
                "status": None,
            })

        return RedirectResponse(home_path())

    def destroy(self, request: Request) -> Response:
        Auth.logout()
        return RedirectResponse("/login")
"""

_AUTH_REGISTER_CONTROLLER = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.auth.manager import Auth, hash_password
from hunt.support.helpers import config
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
        return RedirectResponse(str(config("auth.home", "/") or "/"))
"""

_AUTH_PASSWORD_CONTROLLER = """\
import html
import logging
import os
from urllib.parse import quote

from hunt.auth.passwords import Password
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response, RedirectResponse
from hunt.mail.mailable import Mailable
from hunt.mail.manager import Mail
from hunt.validation.validator import Validator
from app.models.user import User

# The broker stores only a hash of each token, expires tokens after an hour,
# makes them single-use and issues at most one link per address per minute.
Password.set_model(User)

_log = logging.getLogger("app.auth")


def _send_reset_link(email: str, token: str) -> None:
    \"\"\"Send the password reset link through the configured mailer.

    MAIL_MAILER=log (the default) does not deliver email; with APP_ENV=local the
    link is written to the log during development. Configure SMTP in
    .env for real delivery. A delivery failure is logged and never shown to
    the visitor, so the response does not reveal whether the address exists.
    \"\"\"
    base = os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")
    url = f"{base}/reset-password/{quote(token, safe='')}?email={quote(email, safe='')}"
    minutes = Password.TOKEN_EXPIRY // 60
    message = (
        Mailable()
        .subject("Reset your password")
        .html(
            "<p>We received a request to reset the password for your account.</p>"
            f'<p><a href="{html.escape(url, quote=True)}">Choose a new password</a></p>'
            f"<p>The link expires in {minutes} minutes. If you did not ask for it, ignore this email.</p>"
        )
        .text(
            "We received a request to reset the password for your account.\\n\\n"
            f"Choose a new password: {url}\\n\\n"
            f"The link expires in {minutes} minutes. If you did not ask for it, ignore this email.\\n"
        )
    )
    try:
        Mail.to(email).send(message)
    except Exception:
        _log.exception("Could not send the password reset email")


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

        # Returns (address stored on the user record, token), or None for an
        # unknown address or one that was sent a link less than a minute ago.
        created = Password.create_token(data["email"])
        if created is not None:
            stored_email, token = created
            # Always mail the stored address, never the submitted one. In
            # production, queue this (Mail.to(...).queue(...)) so slow SMTP
            # delivery does not make existing addresses distinguishable.
            _send_reset_link(stored_email, token)

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

        # Checks the token, consumes it exactly once, stores the new password
        # hash and signs the user out of their other sessions.
        if not Password.reset(data):
            return self.view("auth.reset_password", {
                "token": data["token"],
                "email": data["email"],
                "errors": {"email": ["This password reset link is invalid or has expired."]},
            })

        return RedirectResponse("/login?reset=1")
"""

_ROUTES_AUTH = """\
from hunt.http.request import Request
from hunt.http.router import Router
from hunt.support.helpers import config
from app.controllers.auth.login_controller import LoginController
from app.controllers.auth.register_controller import RegisterController
from app.controllers.auth.password_controller import ForgotPasswordController, ResetPasswordController
from app.middleware.guest import RedirectIfAuthenticated
from app.middleware.throttle import Throttle


class ThrottleLogin(Throttle):
    \"\"\"5 attempts per minute for one account from one address.\"\"\"

    max_attempts = 5
    decay_seconds = 60

    def _resolve_key(self, request: Request) -> str:
        email = str(request.input("email", "") or "").strip().lower()
        return f"login:{request.ip}:{email}"


class ThrottleLoginAddress(Throttle):
    \"\"\"Caps password guessing across many accounts from one address.\"\"\"

    max_attempts = 30
    decay_seconds = 60

    def _resolve_key(self, request: Request) -> str:
        return f"login-address:{request.ip}"


class ThrottleRegistration(Throttle):
    max_attempts = 5
    decay_seconds = 60

    def _resolve_key(self, request: Request) -> str:
        return f"register:{request.ip}"


class ThrottlePasswordReset(Throttle):
    \"\"\"Shared by the forgot-password and reset-password forms.\"\"\"

    max_attempts = 5
    decay_seconds = 60

    def _resolve_key(self, request: Request) -> str:
        return f"password-reset:{request.ip}"


def register(router: Router) -> None:
    features = config("auth.features", {})
    login_enabled = features.get("login", True)
    registration_enabled = features.get("registration", True)
    forgot_enabled = features.get("forgot_password", True)

    login = LoginController()

    with router.group(middleware=[RedirectIfAuthenticated]):
        if login_enabled:
            router.get("/login", login.show).named("login")
            router.post("/login", login.store).middleware(ThrottleLoginAddress, ThrottleLogin)
        if registration_enabled:
            reg = RegisterController()
            router.get("/register", reg.show).named("register")
            router.post("/register", reg.store).middleware(ThrottleRegistration)
        if forgot_enabled:
            forgot = ForgotPasswordController()
            router.get("/forgot-password", forgot.show).named("password.request")
            router.post("/forgot-password", forgot.store).middleware(ThrottlePasswordReset).named("password.email")

    if login_enabled:
        router.post("/logout", login.destroy).named("logout")
    if forgot_enabled:
        reset = ResetPasswordController()
        router.get("/reset-password/{token}", reset.show).named("password.reset")
        router.post("/reset-password", reset.store).middleware(ThrottlePasswordReset).named("password.update")
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
    # The system tools (sessions, cache, logs, queue) expose operational data
    # and are denied by default. Enable the ones you need, for administrators:
    # Admin.authorize_tool("sessions")
    # Admin.authorize_tool("cache")
    # Admin.authorize_tool("logs")
    # Admin.authorize_tool("queue")
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
    # routes/auth.py imports this module, so `hunt upgrade` must add it alongside.
    "app/middleware/throttle.py": _THROTTLE_MIDDLEWARE,
    "app/admin/__init__.py": "",
    "app/admin/user_resource.py": _ADMIN_USER_RESOURCE,
    "app/console/kernel.py": _CONSOLE_KERNEL,
    "routes/auth.py": _ROUTES_AUTH,
    "routes/admin.py": _ROUTES_ADMIN,
}
