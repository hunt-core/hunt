from __future__ import annotations

import os
from pathlib import Path

import click


def _view_settings() -> tuple[list[Path], Path, str]:
    """Return (view paths, cache directory, extension) exactly as the application resolves them.

    The values come from ``config/view.py`` and are resolved against the
    application base path the same way ``Application`` does, so the file names
    computed here are the ones the template loader looks up at runtime.
    """
    # bootstrap/app.py builds the base path with Path(__file__).resolve(), so resolve here too.
    base = Path.cwd().resolve()
    cfg: dict = {}
    config_dir = base / "config"
    if (config_dir / "view.py").exists():
        try:
            from hunt.config.loader import load_config_directory

            loaded = load_config_directory(config_dir).get("view")
            if isinstance(loaded, dict):
                cfg = loaded
        except Exception as exc:
            raise click.ClickException(f"Could not load config/view.py: {exc}") from exc

    raw_paths = cfg.get("paths") or ["resources/views"]
    paths = [Path(p) if Path(p).is_absolute() else base / p for p in raw_paths]
    raw_cache = cfg.get("cache") or "storage/framework/views"
    cache_dir = Path(raw_cache) if Path(raw_cache).is_absolute() else base / raw_cache
    extension = str(cfg.get("extension") or ".html")
    if not extension.startswith("."):
        extension = f".{extension}"
    return paths, cache_dir, extension


@click.command("view:cache")
def view_cache_command() -> None:
    """Pre-compile all Blade/Jinja templates."""
    from hunt.view.directives import preprocess
    from hunt.view.factory import compiled_view_cache_name, write_compiled_view

    paths, cache_dir, extension = _view_settings()
    existing = [p for p in paths if p.is_dir()]
    if not existing:
        click.echo("  resources/views/ directory not found.", err=True)
        return

    cache_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    failed = 0
    for views_dir in existing:
        for template in sorted(views_dir.rglob(f"*{extension}")):
            if not template.is_file():
                continue
            relative = template.relative_to(views_dir)
            # Same path the template loader builds: <views path>/<relative template path>.
            filename = os.path.join(str(views_dir), *relative.parts)
            try:
                processed = preprocess(template.read_text(encoding="utf-8"), extension)
            except Exception as exc:
                failed += 1
                click.echo(f"  Skipped {relative.as_posix()}: {exc}", err=True)
                continue
            write_compiled_view(cache_dir, compiled_view_cache_name(filename, extension), processed)
            count += 1

    try:
        shown = cache_dir.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        shown = str(cache_dir)
    click.echo(f"  {count} template(s) compiled to {shown}/.")
    if failed:
        raise SystemExit(1)


@click.command("view:clear")
def view_clear_command() -> None:
    """Clear all compiled view templates."""
    from hunt.view.factory import prune_compiled_views

    _, cache_dir, _ = _view_settings()
    # Only files written by the view cache are removed (.gitkeep and the like stay).
    prune_compiled_views(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    click.echo("  Compiled views cleared.")
