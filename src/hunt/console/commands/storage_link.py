from __future__ import annotations

from pathlib import Path

import click


@click.command("storage:link")
def storage_link_command() -> None:
    """Create a symbolic link from public/storage to storage/app/public."""
    cwd = Path.cwd()
    target = cwd / "storage" / "app" / "public"
    link = cwd / "public" / "storage"

    target.mkdir(parents=True, exist_ok=True)

    if link.exists() or link.is_symlink():
        if link.is_symlink() and link.resolve() == target.resolve():
            click.echo("  The [public/storage] link already exists and is correct.")
            return
        click.echo(
            "  [public/storage] already exists. Remove it manually before re-linking.",
            err=True,
        )
        raise SystemExit(1)

    link.symlink_to(target)
    click.echo("  The [public/storage] link has been created.")
