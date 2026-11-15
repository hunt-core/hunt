from __future__ import annotations

import importlib
import sys
from pathlib import Path

import click


def boot_application() -> bool:
    """Prepare the database configuration for a CLI command.

    Boots the project's application (``bootstrap/app.py``) so that
    ``config/database.py`` configures the connections exactly as it does for
    the web process. Without a bootable app, falls back to loading ``.env`` so
    the ``DB_*`` / ``DATABASE_URL`` environment fallbacks work.

    Returns True when the application was booted.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    booted = False
    if (Path.cwd() / "bootstrap" / "app.py").exists():
        try:
            importlib.import_module("bootstrap.app")
            booted = True
        except Exception as exc:
            click.echo(f"  WARNING: failed to boot application: {exc}", err=True)
    if not booted:
        from dotenv import load_dotenv

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)
    return booted


def warn_if_throwaway_database() -> None:
    """Warn when the default connection is an in-memory SQLite database.

    That is what the framework falls back to when nothing configures a
    database; anything a CLI command writes to it is gone when it exits.
    """
    try:
        from hunt.database.connection import connection

        url = connection().url
        if url.get_backend_name() == "sqlite" and (url.database in (None, "", ":memory:")):
            click.echo(
                "  WARNING: the default database is an in-memory SQLite database, which is discarded when this "
                "command exits. Set DB_DATABASE / DATABASE_URL or configure config/database.py.",
                err=True,
            )
    except Exception:
        pass


def is_production() -> bool:
    from hunt.application import app_env

    return app_env().strip().lower() in ("production", "prod")
