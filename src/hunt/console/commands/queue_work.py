from __future__ import annotations

import importlib
import signal
import sys
from pathlib import Path

import click

from hunt.queue.serialization import import_job_class
from hunt.queue.worker import JobTimeout as _JobTimeout
from hunt.queue.worker import WorkerError, run_worker
from hunt.queue.worker import backoff_delay as _backoff_delay
from hunt.queue.worker import record_history as _record_history
from hunt.queue.worker import run_with_timeout as _run_with_timeout

__all__ = [
    "_JobTimeout",
    "_backoff_delay",
    "_record_history",
    "_run_with_timeout",
    "_safe_import",
    "boot_application",
    "queue_work_command",
]


def _safe_import(dotted_path: str, job_cls: type) -> type:
    """Import a dotted class path, validating format and Job subclass constraint."""
    return import_job_class(dotted_path, job_cls)


def boot_application() -> bool:
    """Boot ``bootstrap.app`` from the current directory, or fall back to ``.env``.

    Booting runs the config files (database, queue, mail, ...) and service
    providers, so console work sees the same configuration as the web app.
    Returns True when the application booted.
    """
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    booted = False
    if (Path.cwd() / "bootstrap" / "app.py").exists():
        try:
            importlib.import_module("bootstrap.app")
            booted = True
        except Exception as exc:
            click.echo(f"  WARNING: failed to boot application: {exc}", err=True)
    if not booted:
        # No bootable app — load .env so the env-var fallbacks work.
        from dotenv import load_dotenv

        env_file = Path.cwd() / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)
    return booted


@click.command("queue:work")
@click.option("--queue", default="default", help="Queue name to process")
@click.option("--sleep", default=3, type=int, help="Seconds to sleep when no jobs are available")
@click.option(
    "--tries",
    default=3,
    type=int,
    help="Max attempts before a job is marked as failed (a job class that sets `tries` itself wins)",
)
@click.option("--once", is_flag=True, default=False, help="Process a single job and exit")
@click.option("--max-jobs", default=0, type=int, help="Stop after processing this many jobs (0 = no limit)")
@click.option("--max-time", default=0, type=int, help="Stop after running for this many seconds (0 = no limit)")
@click.option(
    "--driver",
    "driver_name",
    default=None,
    type=click.Choice(["database", "redis"]),
    help="Queue driver to pull jobs from (defaults to QUEUE_DRIVER env)",
)
def queue_work_command(
    queue: str, sleep: int, tries: int, once: bool, max_jobs: int, max_time: int, driver_name: str | None
) -> None:
    """Process queued jobs."""
    # Boot the application so config/queue.py (and the other config files)
    # configure the managers, and event listener allowlists are populated.
    booted = boot_application()

    from hunt.queue.drivers.sync import SyncDriver
    from hunt.queue.manager import Queue, _build_driver

    if driver_name:
        driver = _build_driver(driver_name)
    else:
        driver = Queue._get_driver()
        if isinstance(driver, SyncDriver):
            # Sync jobs run inline at dispatch time and never reach a backend,
            # so a worker polls the database queue instead.
            driver = _build_driver("database")
    name = "redis" if driver.__class__.__name__ == "RedisDriver" else "database"

    # Warn if queued-event allowlists are empty — QueuedEventListener jobs will
    # be rejected at runtime unless the app's EventServiceProvider is booted.
    # When the app booted above, empty allowlists just mean no queued listeners
    # are registered, which is not worth a warning.
    if not booted:
        try:
            from hunt.events.queued import _ALLOWED_EVENT_CLASSES, _ALLOWED_LISTENER_CLASSES

            if not _ALLOWED_LISTENER_CLASSES and not _ALLOWED_EVENT_CLASSES:
                click.echo(
                    "  WARNING: Event listener allowlists are empty. "
                    "QueuedEventListener jobs will fail. "
                    "Boot your application before starting the worker to populate allowlists.",
                    err=True,
                )
        except ImportError:
            pass

    # Graceful drain: on SIGTERM (systemd/Docker stop) or SIGINT (Ctrl+C) finish
    # the job in flight, then exit instead of dying mid-job. A second Ctrl+C
    # aborts immediately; the interrupted job is retried after retry_after.
    _stop = {"requested": False}

    def _request_stop(signum, frame):
        if _stop["requested"] and signum == getattr(signal, "SIGINT", None):
            raise KeyboardInterrupt
        _stop["requested"] = True
        label = "SIGINT" if signum == getattr(signal, "SIGINT", None) else "SIGTERM"
        click.echo(f"\n  {label} received — finishing current job, then stopping.")

    previous_handlers: dict[int, object] = {}
    for sig_name in ("SIGTERM", "SIGINT"):
        signum = getattr(signal, sig_name, None)
        if signum is None:
            continue
        try:
            previous_handlers[signum] = signal.signal(signum, _request_stop)
        except (ValueError, OSError):
            pass  # not in the main thread

    click.echo(f"  Processing queue: {queue} (driver: {name}). Press Ctrl+C to stop.")
    try:
        run_worker(
            driver,
            queue=queue,
            sleep=sleep,
            tries=tries,
            once=once,
            max_jobs=max_jobs,
            max_time=max_time,
            should_stop=lambda: _stop["requested"],
            output=click.echo,
        )
        if _stop["requested"]:
            click.echo("  Worker stopped.")
    except KeyboardInterrupt:
        click.echo("\n  Worker stopped.")
    except WorkerError as exc:
        click.echo(f"  ERROR: {exc}", err=True)
        raise SystemExit(1) from exc
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError, TypeError):
                pass
