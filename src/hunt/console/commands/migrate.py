from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from hunt.console.commands.db.boot import boot_application, is_production, warn_if_throwaway_database


def _load_env() -> None:
    """Boot the app (or load .env) so the configured database is the one migrated."""
    boot_application()


def _get_migrator(warn: bool = True) -> Any:
    _load_env()
    if warn:
        warn_if_throwaway_database()
    from hunt.database.schema.migration import Migrator

    migrations_path = Path.cwd() / "database" / "migrations"
    return Migrator(migrations_path)


def _run_pending() -> None:
    from hunt.database.schema.migration import MigrationError

    migrator = _get_migrator()
    try:
        ran = migrator.run()
    except MigrationError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    if not ran:
        click.echo("Nothing to migrate.")
    else:
        for name in ran:
            click.echo(f"  Migrated: {name}")


def _refuse_in_production(action: str, force: bool) -> None:
    if is_production() and not force:
        click.echo(
            f"  ERROR: refusing to {action} while APP_ENV=production. Re-run with --force if you really mean it.",
            err=True,
        )
        raise SystemExit(1)


@click.group("migrate", invoke_without_command=True)
@click.pass_context
def migrate_group(ctx: click.Context) -> None:
    """Run pending migrations (or use a sub-command: rollback, fresh, status)."""
    if ctx.invoked_subcommand is None:
        _run_pending()


@migrate_group.command("run")
def migrate_run() -> None:
    """Run pending migrations."""
    _run_pending()


@migrate_group.command("rollback")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Allow in production, and drop tracking rows whose migration file is missing.",
)
def migrate_rollback(force: bool) -> None:
    """Rollback the last batch of migrations."""
    from hunt.database.schema.migration import MigrationError

    migrator = _get_migrator()
    _refuse_in_production("roll back migrations", force)
    try:
        rolled = migrator.rollback(force=force)
    except MigrationError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    if not rolled:
        click.echo("Nothing to rollback.")
    else:
        for name in rolled:
            click.echo(f"  Rolled back: {name}")


@migrate_group.command("fresh")
@click.option("--force", is_flag=True, default=False, help="Allow in production.")
@click.confirmation_option(prompt="This will drop all tables. Continue?")
def migrate_fresh(force: bool) -> None:
    """Drop all tables and re-run all migrations."""
    from hunt.database.schema.migration import MigrationError

    migrator = _get_migrator()
    _refuse_in_production("drop all tables", force)
    try:
        ran = migrator.fresh()
    except MigrationError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    for name in ran:
        click.echo(f"  Migrated: {name}")


@migrate_group.command("status")
@click.option("--pending", is_flag=True, default=False, help="Exit 1 if any migrations are pending (for CI/CD).")
def migrate_status(pending: bool) -> None:
    """Show status of all migrations."""
    migrator = _get_migrator()
    statuses = migrator.status()
    if not statuses:
        click.echo("No migrations found.")
        return
    click.echo(f"{'Migration':<50} {'Status'}")
    click.echo("-" * 60)
    has_pending = False
    for s in statuses:
        if not s["ran"]:
            has_pending = True
        if s.get("dirty"):
            status = click.style("Dirty (started, not finished)", fg="red")
        elif s["ran"]:
            status = click.style("Ran", fg="green")
        else:
            status = click.style("Pending", fg="yellow")
        click.echo(f"{s['migration']:<50} {status}")
    if pending and has_pending:
        sys.exit(1)
