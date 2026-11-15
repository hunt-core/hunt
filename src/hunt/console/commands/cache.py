from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click


def _boot_application() -> bool:
    """Import the app's bootstrap so the configured cache store is the one acted on.

    Returns False when the working directory is not a hunt application.
    """
    cwd = Path.cwd()
    if not (cwd / "bootstrap" / "app.py").exists():
        return False
    import importlib

    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))
    importlib.import_module("bootstrap.app")
    return True


def _boot_or_exit() -> bool:
    try:
        return _boot_application()
    except Exception as exc:
        click.echo(f"  Could not boot the application: {exc}", err=True)
        raise SystemExit(1) from exc


@click.group("cache")
def cache_group() -> None:
    """Cache management commands."""


@cache_group.command("clear")
def cache_clear() -> None:
    """Clear the application cache."""
    booted = _boot_or_exit()
    if booted:
        # Flush the store the application is configured with (file, Redis, ...).
        from hunt.cache.manager import Cache

        try:
            Cache.flush()
        except Exception as exc:
            click.echo(f"  Could not flush the cache store: {exc}", err=True)
            raise SystemExit(1) from exc

    cache_dir = Path.cwd() / "storage" / "framework" / "cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True)
    views_cache = Path.cwd() / "storage" / "framework" / "views"
    if views_cache.exists():
        shutil.rmtree(views_cache)
        views_cache.mkdir(parents=True)
    click.echo("  Cache cleared.")


@cache_group.command("forget")
@click.argument("key")
def cache_forget(key: str) -> None:
    """Remove a specific key from the cache."""
    if not _boot_or_exit():
        click.echo(
            "  Not a hunt application (bootstrap/app.py not found): there is no configured cache store to act on.",
            err=True,
        )
        raise SystemExit(1)

    from hunt.cache.manager import Cache

    try:
        Cache.forget(key)
    except Exception as exc:
        click.echo(f"  Could not remove {key}: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"  Removed: {key}")
