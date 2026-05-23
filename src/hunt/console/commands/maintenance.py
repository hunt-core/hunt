from __future__ import annotations

import json
from pathlib import Path

import click

_SENTINEL = ".maintenance"


@click.command("down")
@click.option("--message", default="We'll be back shortly. Please try again later.", help="Message shown to visitors.")
@click.option("--retry", default=60, type=int, help="Retry-After seconds sent in the response header.")
def down_command(message: str, retry: int) -> None:
    """Put the application into maintenance mode (returns 503 to all visitors)."""
    sentinel = Path.cwd() / _SENTINEL
    sentinel.write_text(json.dumps({"message": message, "retry_after": retry}, indent=2))
    click.echo("  Application is now in maintenance mode.")
    click.echo(f"  Message : {message}")
    click.echo(f"  Retry-After : {retry}s")
    click.echo("  Run `hunt up` to bring it back online.")


@click.command("up")
def up_command() -> None:
    """Take the application out of maintenance mode."""
    sentinel = Path.cwd() / _SENTINEL
    if sentinel.exists():
        sentinel.unlink()
        click.echo("  Application is now live.")
    else:
        click.echo("  Application is already live (no .maintenance file found).")
