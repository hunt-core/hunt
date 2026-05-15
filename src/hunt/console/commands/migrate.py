from __future__ import annotations

from pathlib import Path

import click


def _load_env() -> None:
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def _get_migrator() -> any:
    _load_env()
    from hunt.database.schema.migration import Migrator

    migrations_path = Path.cwd() / "database" / "migrations"
    return Migrator(migrations_path)


@click.group("migrate", invoke_without_command=True)
@click.pass_context
def migrate_group(ctx: click.Context) -> None:
    """Run pending migrations (or use a sub-command: rollback, fresh, status)."""
    if ctx.invoked_subcommand is None:
        migrator = _get_migrator()
        ran = migrator.run()
        if not ran:
            click.echo("Nothing to migrate.")
        else:
            for name in ran:
                click.echo(f"  Migrated: {name}")


@migrate_group.command("run")
def migrate_run() -> None:
    """Run pending migrations."""
    migrator = _get_migrator()
    ran = migrator.run()
    if not ran:
        click.echo("Nothing to migrate.")
    else:
        for name in ran:
            click.echo(f"  Migrated: {name}")


@migrate_group.command("rollback")
def migrate_rollback() -> None:
    """Rollback the last batch of migrations."""
    migrator = _get_migrator()
    rolled = migrator.rollback()
    if not rolled:
        click.echo("Nothing to rollback.")
    else:
        for name in rolled:
            click.echo(f"  Rolled back: {name}")


@migrate_group.command("fresh")
@click.confirmation_option(prompt="This will drop all tables. Continue?")
def migrate_fresh() -> None:
    """Drop all tables and re-run all migrations."""
    migrator = _get_migrator()
    ran = migrator.fresh()
    for name in ran:
        click.echo(f"  Migrated: {name}")


@migrate_group.command("status")
def migrate_status() -> None:
    """Show status of all migrations."""
    migrator = _get_migrator()
    statuses = migrator.status()
    if not statuses:
        click.echo("No migrations found.")
        return
    click.echo(f"{'Migration':<50} {'Status'}")
    click.echo("-" * 60)
    for s in statuses:
        status = click.style("Ran", fg="green") if s["ran"] else click.style("Pending", fg="yellow")
        click.echo(f"{s['migration']:<50} {status}")
