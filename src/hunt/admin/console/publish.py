from __future__ import annotations

import shutil
from pathlib import Path

import click


@click.command("admin:publish")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing files without prompting.",
)
def admin_publish_command(force: bool) -> None:
    """Copy admin templates to resources/views/admin/ for customisation."""
    src = Path(__file__).parent.parent / "templates" / "admin"
    dest = Path.cwd() / "resources" / "views" / "admin"

    if not src.is_dir():
        click.echo("  Error: admin template source directory not found.", err=True)
        raise SystemExit(1)

    if dest.exists() and not force:
        click.echo(
            f"  Destination {dest.relative_to(Path.cwd())} already exists.\n  Use --force to overwrite existing files."
        )
        return

    copied = 0
    skipped = 0
    for src_file in src.rglob("*"):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src)
        dest_file = dest / rel
        if dest_file.exists() and not force:
            skipped += 1
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        click.echo(f"  Published: resources/views/admin/{rel}")
        copied += 1

    click.echo(f"\n  {copied} file(s) published to resources/views/admin/")
    if skipped:
        click.echo(f"  {skipped} file(s) skipped (already exist — use --force to overwrite).")
