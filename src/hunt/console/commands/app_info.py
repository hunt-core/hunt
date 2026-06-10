from __future__ import annotations

import json
import os
from pathlib import Path

import click

from hunt import __version__


def _count_py_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for f in directory.glob("*.py") if not f.name.startswith("_"))


def _load_env() -> None:
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def _drivers() -> dict:
    """Report the configured drivers from config/*.py, falling back to env."""
    cfg: dict = {}
    try:
        from hunt.config.loader import load_config_directory

        cfg = load_config_directory(Path.cwd() / "config")
    except Exception:
        pass

    def section(name: str) -> dict:
        value = cfg.get(name)
        return value if isinstance(value, dict) else {}

    return {
        "database": section("database").get("default") or os.environ.get("DB_CONNECTION", "(not set)"),
        "session": section("session").get("driver") or os.environ.get("SESSION_DRIVER", "file"),
        "queue": section("queue").get("driver") or os.environ.get("QUEUE_DRIVER", "sync"),
        "cache": section("cache").get("driver") or os.environ.get("CACHE_DRIVER", "file"),
        "mail": section("mail").get("default") or os.environ.get("MAIL_MAILER", "(not set)"),
        "log": section("logging").get("default") or os.environ.get("LOG_CHANNEL", "file"),
    }


def _migration_summary() -> dict:
    try:
        from hunt.database.schema.migration import Migrator

        migrator = Migrator(Path.cwd() / "database" / "migrations")
        statuses = migrator.status()
        ran = sum(1 for s in statuses if s["ran"])
        return {"ran": ran, "pending": len(statuses) - ran, "total": len(statuses)}
    except Exception:
        return {"ran": 0, "pending": 0, "total": 0}


def _route_count() -> int:
    try:
        import sys

        sys.path.insert(0, os.getcwd())
        from bootstrap.app import application  # type: ignore[import]

        router = application.make("router")
        return len(router.routes())
    except Exception:
        return -1


@click.command("app:info")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def app_info_command(as_json: bool) -> None:
    """Show a summary of the current hunt application."""
    _load_env()
    cwd = Path.cwd()

    info = {
        "framework_version": __version__,
        "app_name": os.environ.get("APP_NAME", "(not set)"),
        "app_env": os.environ.get("APP_ENV", "production"),
        "app_debug": os.environ.get("APP_DEBUG", "false"),
        "app_url": os.environ.get("APP_URL", "(not set)"),
        "php_python_version": _python_version(),
        "counts": {
            "routes": _route_count(),
            "models": _count_py_files(cwd / "app" / "models"),
            "controllers": _count_py_files(cwd / "app" / "controllers"),
            "middleware": _count_py_files(cwd / "app" / "middleware"),
            "providers": _count_py_files(cwd / "app" / "providers"),
            "jobs": _count_py_files(cwd / "app" / "jobs"),
            "migrations": _migration_summary(),
        },
        "drivers": _drivers(),
    }

    if as_json:
        click.echo(json.dumps(info, indent=2))
        return

    _print_info(info)


def _python_version() -> str:
    import platform

    return platform.python_version()


def _print_info(info: dict) -> None:
    click.echo("")
    click.echo(f"  hunt {info['framework_version']}  •  Python {info['php_python_version']}")
    click.echo("")

    _section("Application")
    _row("Name", info["app_name"])
    _row("Environment", info["app_env"])
    _row("Debug", info["app_debug"])
    _row("URL", info["app_url"])

    _section("Counts")
    c = info["counts"]
    mig = c["migrations"]
    route_val = str(c["routes"]) if c["routes"] >= 0 else "(could not load)"
    _row("Routes", route_val)
    _row("Models", str(c["models"]))
    _row("Controllers", str(c["controllers"]))
    _row("Middleware", str(c["middleware"]))
    _row("Providers", str(c["providers"]))
    _row("Jobs", str(c["jobs"]))
    _row("Migrations", f"{mig['ran']} ran / {mig['pending']} pending  ({mig['total']} total)")

    _section("Drivers")
    for label, value in info["drivers"].items():
        _row(label.capitalize(), value)
    click.echo("")


def _section(title: str) -> None:
    click.echo(f"\n  {click.style(title, bold=True)}")
    click.echo("  " + "-" * 40)


def _row(label: str, value: str) -> None:
    click.echo(f"  {label:<20} {value}")
