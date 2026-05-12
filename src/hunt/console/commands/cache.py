from __future__ import annotations

import shutil
from pathlib import Path

import click


@click.group("cache")
def cache_group() -> None:
    """Cache management commands."""


@cache_group.command("clear")
def cache_clear() -> None:
    """Clear the application cache."""
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
    from hunt.cache.manager import Cache
    Cache.forget(key)
    click.echo(f"  Removed: {key}")
