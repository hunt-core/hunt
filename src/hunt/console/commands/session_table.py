from __future__ import annotations

import time
from pathlib import Path

import click

_MIGRATION_STUB = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateUserSessionsTable(Migration):
    def up(self) -> None:
        def blueprint(table):
            table.string("id", 64).primary_key()
            table.big_integer("user_id")
            table.string("guard", 32).default("web")
            table.string("ip_address", 45).nullable()
            table.text("user_agent").nullable()
            table.integer("last_active_at")
            table.index("user_id")
            table.index("guard")

        Schema.create("user_sessions", blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("user_sessions")
"""


@click.command("session:table")
def session_table_command() -> None:
    """Create a migration for the user_sessions table."""
    ts = time.strftime("%Y%m%d%H%M%S")
    filename = f"{ts}_create_user_sessions_table.py"
    out = Path.cwd() / "database" / "migrations" / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_MIGRATION_STUB, encoding="utf-8")
    click.echo(f"  Created migration: database/migrations/{filename}")
    click.echo("  Run `hunt migrate` to apply it.")
