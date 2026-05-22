from __future__ import annotations

import shutil
from pathlib import Path

import click

_TAGS: dict[str, tuple[Path, Path]] = {}


def _components_src() -> Path:
    return Path(__file__).parent.parent.parent / "views" / "components"


@click.command("vendor:publish")
@click.option("--tag", default=None, metavar="TAG", help="Publish a specific asset group (e.g. components).")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def vendor_publish_command(tag: str | None, force: bool) -> None:
    """Publish framework assets into the application for customisation."""
    if tag == "components" or tag is None:
        _publish_components(force)
    else:
        click.echo(f"  Unknown tag '{tag}'. Available tags: components", err=True)
        raise SystemExit(1)


def _publish_components(force: bool) -> None:
    src = _components_src()
    if not src.is_dir():
        click.echo("  Error: built-in components directory not found.", err=True)
        raise SystemExit(1)

    dest = Path.cwd() / "resources" / "views" / "components"
    copied = 0
    skipped = 0
    for src_file in sorted(src.rglob("*")):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src)
        dest_file = dest / rel
        if dest_file.exists() and not force:
            skipped += 1
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        click.echo(f"  Published: resources/views/components/{rel}")
        copied += 1

    click.echo(f"\n  {copied} component(s) published to resources/views/components/")
    if skipped:
        click.echo(f"  {skipped} file(s) skipped (already exist — use --force to overwrite).")
