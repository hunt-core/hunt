from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import click


@click.command("view:cache")
def view_cache_command() -> None:
    """Pre-compile all Blade/Jinja templates."""
    from hunt.view.directives import preprocess

    views_dir = Path.cwd() / "resources" / "views"
    if not views_dir.exists():
        click.echo("  resources/views/ directory not found.", err=True)
        return

    cache_dir = Path.cwd() / "storage" / "framework" / "views"
    cache_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for template in sorted(views_dir.rglob("*.html")):
        source = template.read_text(encoding="utf-8")
        processed = preprocess(source)
        mtime = template.stat().st_mtime
        cache_key = hashlib.sha256(f"{template}:{mtime}".encode()).hexdigest()
        (cache_dir / cache_key).write_text(processed, encoding="utf-8")
        count += 1

    click.echo(f"  {count} template(s) compiled to storage/framework/views/.")


@click.command("view:clear")
def view_clear_command() -> None:
    """Clear all compiled view templates."""
    cache_dir = Path.cwd() / "storage" / "framework" / "views"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True)
    click.echo("  Compiled views cleared.")
