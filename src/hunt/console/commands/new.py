from __future__ import annotations

import shutil
from pathlib import Path

import click

from hunt.support.str import Str


@click.command("new")
@click.argument("name")
@click.option("--force", is_flag=True, help="Overwrite existing directory")
def new_command(name: str, force: bool) -> None:
    """Create a new hunt application skeleton."""
    target = Path.cwd() / name
    if target.exists():
        if not force:
            click.echo(f"Directory '{name}' already exists. Use --force to overwrite.", err=True)
            raise SystemExit(1)
        shutil.rmtree(target)

    dirs = [
        "app/controllers",
        "app/models",
        "app/middleware",
        "app/providers",
        "app/requests",
        "app/events",
        "app/listeners",
        "app/jobs",
        "config",
        "database/migrations",
        "database/factories",
        "database/seeders",
        "resources/views/errors",
        "routes",
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
    _write_secret(target / ".env", _ENV_EXAMPLE)  # 0o600 — owner-read only
    _write(target / "config" / "app.py", _CONFIG_APP)
    _write(target / "config" / "database.py", _CONFIG_DATABASE)
    _write(target / "config" / "view.py", _CONFIG_VIEW)
    _write(target / "routes" / "web.py", _ROUTES_WEB)
    _write(target / "routes" / "api.py", _ROUTES_API)
    _write(target / "bootstrap" / "__init__.py", "")
    _write(target / "bootstrap" / "app.py", _BOOTSTRAP_APP)
    _write(target / "app" / "providers" / "app_service_provider.py", _APP_PROVIDER)
    _write(target / "app" / "controllers" / "welcome_controller.py", _WELCOME_CONTROLLER)
    _write(target / "resources" / "views" / "welcome.html", _WELCOME_VIEW)
    _write(target / "resources" / "views" / "layout.html", _LAYOUT_VIEW)
    _write(target / "public" / "index.py", _PUBLIC_INDEX)
    _write(target / "tests" / "__init__.py", "")

    click.echo(f"\n  Application [{name}] created successfully.\n")
    click.echo("  Get started:")
    click.echo(f"    cd {name}")
    click.echo("    uv venv && uv pip install -e .")
    click.echo("    hunt key:generate")
    click.echo("    hunt migrate")
    click.echo("    hunt serve\n")


def _write(path: Path, content: str) -> None:
    path.write_text(content)


def _write_secret(path: Path, content: str) -> None:
    """Write a file and restrict permissions to owner-read/write only."""
    path.write_text(content)
    path.chmod(0o600)


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

_ENV_EXAMPLE = """\
APP_NAME=hunt
APP_ENV=local
APP_KEY=
APP_DEBUG=true
APP_URL=http://localhost:8000

DB_CONNECTION=sqlite
DB_DATABASE=database/database.sqlite

LOG_CHANNEL=file
LOG_LEVEL=debug

QUEUE_DRIVER=sync

CACHE_DRIVER=file
SESSION_LIFETIME=7200
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
from hunt.view.factory import ViewFactory
from hunt.exceptions.handler import ExceptionHandler
from hunt.log.manager import Log
from hunt.cache.manager import Cache
from hunt.support.helpers import _set_app

BASE_PATH = Path(__file__).resolve().parent.parent

application = Application(BASE_PATH)
_set_app(application)

# -- Logging
Log.configure(
    log_path=BASE_PATH / "storage" / "logs" / "hunt.log",
    level=os.environ.get("LOG_LEVEL", "debug"),
)

# -- Cache
Cache.configure(
    driver=os.environ.get("CACHE_DRIVER", "file"),
    path=BASE_PATH / "storage" / "framework" / "cache",
)

# -- Router
router = Router()
application.instance("router", router)

# -- Load routes
from routes.web import register as web_routes
from routes.api import register as api_routes
web_routes(router)
api_routes(router)

# -- Register named routes
for route in router.routes():
    if route.name:
        router._named[route.name] = route

# -- View factory
views_path = BASE_PATH / "resources" / "views"
cache_path = BASE_PATH / "storage" / "framework" / "views"
view_factory = ViewFactory(views_path, cache_path)
application.instance("view", view_factory)

# -- Exception handler
debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
exc_handler = ExceptionHandler(debug=debug, views_path=views_path)

# -- Global middleware
global_middleware = [
    StartSession,
    VerifyCsrfToken,
]

# -- HTTP Kernel (ASGI app)
kernel = HttpKernel(router, global_middleware=global_middleware, exception_handler=exc_handler)
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
    <title>{{ config.get('app.name', 'hunt') if config is defined else 'hunt' }}</title>
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
