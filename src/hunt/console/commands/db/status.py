from __future__ import annotations

import json
from pathlib import Path

import click


def _get_migrator():
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    from hunt.database.schema.migration import Migrator

    return Migrator(Path.cwd() / "database" / "migrations")


@click.command("db:status")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON array.")
def db_status_command(as_json: bool) -> None:
    """Show the run/pending status of every migration file."""
    try:
        migrator = _get_migrator()
        statuses = migrator.status()
    except Exception as e:
        click.echo(f"  Could not read migration status: {e}", err=True)
        raise SystemExit(1) from e

    if not statuses:
        if as_json:
            click.echo("[]")
        else:
            click.echo("  No migrations found.")
        return

    if as_json:
        click.echo(json.dumps(statuses, indent=2))
        return

    ran_count = sum(1 for s in statuses if s["ran"])
    total = len(statuses)
    click.echo(f"  Migrations: {ran_count}/{total} ran\n")
    click.echo(f"  {'Migration':<55} Status")
    click.echo("  " + "-" * 65)
    for s in statuses:
        label = click.style("Ran", fg="green") if s["ran"] else click.style("Pending", fg="yellow")
        click.echo(f"  {s['migration']:<55} {label}")
