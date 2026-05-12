from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import click


@click.command("key:generate")
@click.option("--show", is_flag=True, default=False, help="Print key without writing to .env")
def key_generate_command(show: bool) -> None:
    """Generate a new application key and write it to .env."""
    key = "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode()

    if show:
        click.echo(key)
        return

    env_file = Path.cwd() / ".env"
    if not env_file.exists():
        click.echo("  No .env file found. Run `hunt new` to scaffold a project first.")
        return

    content = env_file.read_text()
    if "APP_KEY=" in content:
        content = re.sub(r"APP_KEY=.*", f"APP_KEY={key}", content)
    else:
        content += f"\nAPP_KEY={key}\n"

    env_file.write_text(content)
    click.echo(f"  Application key set: {key}")
