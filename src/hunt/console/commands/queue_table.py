from __future__ import annotations

import time
from pathlib import Path

import click

_MIGRATION_STUB = """\
from hunt.database.schema.migration import Migration
from hunt.database.schema.builder import Schema


class CreateJobsTables(Migration):
    def up(self) -> None:
        def jobs_blueprint(table):
            table.id()
            table.string("queue", 255).default("default")
            table.text("payload")
            table.small_integer("attempts").default(0)
            table.integer("reserved_at").nullable()
            table.integer("available_at").nullable()
            table.integer("created_at")
            table.index("queue")

        Schema.create("jobs", jobs_blueprint)

        def failed_blueprint(table):
            table.id()
            table.string("uuid", 36)
            table.string("connection", 255)
            table.string("queue", 255)
            table.text("payload")
            table.text("exception")
            table.integer("failed_at")
            table.unique("uuid")

        Schema.create("jobs_failed", failed_blueprint)

        def history_blueprint(table):
            table.id()
            table.string("job_class", 255)
            table.string("queue", 255).default("default")
            table.integer("duration_ms")
            table.integer("finished_at")
            table.string("status", 20)  # 'completed' or 'failed'
            table.index("queue")
            table.index("finished_at")
            table.index("status")

        Schema.create("jobs_history", history_blueprint)

    def down(self) -> None:
        Schema.drop_if_exists("jobs_history")
        Schema.drop_if_exists("jobs_failed")
        Schema.drop_if_exists("jobs")
"""


@click.command("queue:table")
def queue_table_command() -> None:
    """Create a migration for the jobs and jobs_failed tables."""
    ts = time.strftime("%Y%m%d%H%M%S")
    filename = f"{ts}_create_jobs_tables.py"
    out = Path.cwd() / "database" / "migrations" / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_MIGRATION_STUB, encoding="utf-8")
    click.echo(f"  Created migration: database/migrations/{filename}")
    click.echo("  Run `hunt migrate` to apply it.")
