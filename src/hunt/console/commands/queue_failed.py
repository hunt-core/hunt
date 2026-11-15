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


def _driver_for(connection_name: str):
    """Return the queue driver a failed job came from (``redis`` or ``database``)."""
    from hunt.queue.manager import Queue, _build_driver

    wanted = "RedisDriver" if connection_name == "redis" else "DatabaseDriver"
    configured = Queue._get_driver()
    if configured.__class__.__name__ == wanted:
        return configured
    return _build_driver("redis" if connection_name == "redis" else "database")


def _retry_row(row) -> bool:
    """Re-sign a failed job and move it back to the driver it came from.

    Returns False when the failed row had already been retried or removed.
    """
    from hunt.queue.serialization import derive_job_id, open_payload, valid_job_id

    # Accepts payloads signed before the queue.v1 signing upgrade as well, so
    # this command is the way to bring pre-upgrade jobs forward.
    body = open_payload(row.payload, allow_legacy=True, check_age=False)
    # A retry is a new delivery with a new identity and a new timestamp: the
    # failed job's own id is recorded as finished and can never run again.
    # The new id is derived from the old one, so if the same failed payload is
    # retried twice (a copied jobs_failed row, or a crash between enqueueing
    # and deleting the row) both retries carry one id and it runs once.
    old_id = body.pop("id", None)
    body.pop("created_at", None)
    body.pop("available_at", None)
    if valid_job_id(old_id):
        body["id"] = derive_job_id("retry", old_id)

    # Older jobs_failed tables have no ``connection`` column: those are database jobs.
    if getattr(row, "connection", None) == "redis":
        # Two stores, so no shared transaction: enqueue first, then delete. A
        # crash in between leaves the failed row behind rather than losing the job.
        _driver_for("redis").push_payload(body, row.queue)
        raw("DELETE FROM jobs_failed WHERE id = :id", {"id": row.id})
        return True
    return bool(_driver_for("database").retry_failed(row.id, body, row.queue))


@click.command("queue:retry")
@click.argument("id", type=int, required=False)
@click.option("--all", "retry_all", is_flag=True, default=False, help="Retry every failed job")
def queue_retry_command(id: int | None, retry_all: bool) -> None:
    """Retry a failed job by its ID (or all of them with --all)."""
    _load_env()
    from hunt.queue.serialization import InvalidPayload

    if retry_all:
        rows = raw("SELECT * FROM jobs_failed ORDER BY id").fetchall()
    elif id is None:
        raise click.UsageError("Pass a failed job ID, or --all.")
    else:
        row = raw("SELECT * FROM jobs_failed WHERE id = :id", {"id": id}).fetchone()
        if row is None:
            click.echo(f"  No failed job with id {id}.", err=True)
            raise SystemExit(1)
        rows = [row]

    errors = 0
    for row in rows:
        try:
            requeued = _retry_row(row)
        except InvalidPayload as exc:
            errors += 1
            click.echo(f"  Failed job [{row.id}] cannot be retried: {exc}", err=True)
            continue
        if not requeued:
            click.echo(f"  Failed job [{row.id}] was already retried or removed.")
            continue
        click.echo(f"  Failed job [{row.id}] has been pushed back onto the [{row.queue}] queue.")
    if retry_all and not rows:
        click.echo("  No failed jobs found.")
    if errors:
        raise SystemExit(1)


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


@click.command("queue:prune-processed")
@click.option(
    "--driver",
    "driver_name",
    default=None,
    type=click.Choice(["database", "redis"]),
    help="Queue driver whose processed-job ids to prune (defaults to the configured driver)",
)
def queue_prune_processed_command(driver_name: str | None) -> None:
    """Forget the ids of jobs that finished longer ago than the retention window.

    Workers do this themselves every few minutes; the command is for cron or
    for pruning right after lowering QUEUE_PROCESSED_RETENTION.
    """
    from hunt.console.commands.queue_work import boot_application
    from hunt.queue.drivers.sync import SyncDriver
    from hunt.queue.manager import Queue, _build_driver
    from hunt.queue.serialization import keep_processed_for

    boot_application()
    if driver_name:
        driver = _build_driver(driver_name)
    else:
        driver = Queue._get_driver()
        if isinstance(driver, SyncDriver):
            driver = _build_driver("database")

    if driver.__class__.__name__ == "RedisDriver":
        click.echo("  Nothing to prune: Redis expires processed job ids by itself.")
        return
    if not driver.processed_store_ready():
        click.echo(f"  Cannot prune: {driver.processed_store_hint()}.", err=True)
        raise SystemExit(1)
    keep = keep_processed_for(driver.processed_retention)
    if keep == 0:
        click.echo("  Nothing pruned: QUEUE_PROCESSED_RETENTION is 0, so processed job ids are kept forever.")
        return
    removed = driver.prune_processed()
    click.echo(f"  Pruned {removed} processed job id(s) older than {keep} seconds.")
