from __future__ import annotations

import sys
import time
from pathlib import Path

import click

# Module-level for patchability in tests
try:
    from hunt.database.connection import raw
except Exception:  # pragma: no cover
    raw = None  # type: ignore[assignment]


def _load_env() -> None:
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    sys.path.insert(0, str(Path.cwd()))


@click.command("queue:failed")
def queue_failed_command() -> None:
    """List all failed jobs."""
    _load_env()
    result = raw("SELECT id, uuid, queue, exception, failed_at FROM jobs_failed ORDER BY id")
    rows = result.fetchall()
    if not rows:
        click.echo("  No failed jobs found.")
        return

    click.echo(f"  {'ID':<6} {'UUID':<38} {'Queue':<12} {'Failed At':<22} Exception")
    click.echo("  " + "-" * 100)
    for row in rows:
        failed_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row.failed_at))
        exc_preview = str(row.exception)[:60].replace("\n", " ")
        click.echo(f"  {row.id:<6} {row.uuid:<38} {row.queue:<12} {failed_at:<22} {exc_preview}")


@click.command("queue:retry")
@click.argument("id", type=int)
def queue_retry_command(id: int) -> None:
    """Retry a failed job by its ID."""
    _load_env()
    result = raw("SELECT * FROM jobs_failed WHERE id = :id", {"id": id})
    row = result.fetchone()
    if row is None:
        click.echo(f"  No failed job with id {id}.", err=True)
        raise SystemExit(1)

    raw(
        "INSERT INTO jobs (queue, payload, attempts, available_at, created_at)"
        " VALUES (:queue, :payload, 0, NULL, :now)",
        {"queue": row.queue, "payload": row.payload, "now": int(time.time())},
    )
    raw("DELETE FROM jobs_failed WHERE id = :id", {"id": id})
    click.echo(f"  Failed job [{id}] has been pushed back onto the [{row.queue}] queue.")


@click.command("queue:flush")
@click.option("--hours", default=None, type=float, help="Only flush jobs older than this many hours")
def queue_flush_command(hours: float | None) -> None:
    """Delete all failed jobs (or those older than --hours)."""
    _load_env()
    if hours is not None:
        cutoff = int(time.time()) - int(hours * 3600)
        raw("DELETE FROM jobs_failed WHERE failed_at <= :cutoff", {"cutoff": cutoff})
        click.echo(f"  Flushed failed jobs older than {hours} hour(s).")
    else:
        raw("DELETE FROM jobs_failed")
        click.echo("  All failed jobs have been flushed.")
