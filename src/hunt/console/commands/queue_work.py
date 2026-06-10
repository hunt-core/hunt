from __future__ import annotations

import importlib
import json
import re
import signal
import sys
import threading
import time
from pathlib import Path

import click

_HAS_SIGALRM = hasattr(signal, "SIGALRM")


class _JobTimeout(Exception):
    pass


def _run_with_timeout(fn, timeout_seconds: int) -> None:
    """Run *fn()* and raise _JobTimeout if it takes longer than *timeout_seconds*."""
    if timeout_seconds <= 0:
        fn()
        return

    if _HAS_SIGALRM:

        def _handler(signum, frame):
            raise _JobTimeout(f"Job timed out after {timeout_seconds}s")

        old = signal.signal(signal.SIGALRM, _handler)
        signal.alarm(timeout_seconds)
        try:
            fn()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
    else:
        exc_box: list[BaseException | None] = [None]

        def _target():
            try:
                fn()
            except BaseException as e:
                exc_box[0] = e

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout_seconds)
        if t.is_alive():
            raise _JobTimeout(f"Job timed out after {timeout_seconds}s")
        if exc_box[0] is not None:
            raise exc_box[0]


_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")


def _record_history(job_class: str, queue: str, duration_ms: int, status: str) -> None:
    """Write a completed/failed record to jobs_history if the table exists."""
    try:
        from hunt.database.connection import raw as db_raw

        db_raw(
            "INSERT INTO jobs_history (job_class, queue, duration_ms, finished_at, status)"
            " VALUES (:job_class, :queue, :duration_ms, :finished_at, :status)",
            {
                "job_class": job_class,
                "queue": queue,
                "duration_ms": duration_ms,
                "finished_at": int(time.time()),
                "status": status,
            },
        )
    except Exception:
        pass  # Table may not exist; don't crash the worker


def _safe_import(dotted_path: str, job_cls: type) -> type:
    """Import a dotted class path, validating format and Job subclass constraint."""
    if not _DOTTED_PATH_RE.match(dotted_path):
        raise ValueError(f"Malformed job class path: {dotted_path!r}")
    module_path, class_name = dotted_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    if not (isinstance(cls, type) and issubclass(cls, job_cls)):
        raise TypeError(f"Refusing to execute non-Job class: {dotted_path!r}")
    return cls


def _backoff_delay(backoff: int | list[int], attempt: int) -> int:
    if isinstance(backoff, list):
        idx = min(attempt - 1, len(backoff) - 1)
        return int(backoff[idx])
    return int(backoff)


@click.command("queue:work")
@click.option("--queue", default="default", help="Queue name to process")
@click.option("--sleep", default=3, type=int, help="Seconds to sleep when no jobs are available")
@click.option("--tries", default=3, type=int, help="Max attempts before marking a job as failed")
@click.option("--once", is_flag=True, default=False, help="Process a single job and exit")
@click.option(
    "--driver",
    "driver_name",
    default=None,
    type=click.Choice(["database", "redis"]),
    help="Queue driver to pull jobs from (defaults to QUEUE_DRIVER env)",
)
def queue_work_command(queue: str, sleep: int, tries: int, once: bool, driver_name: str | None) -> None:
    """Process queued jobs."""
    sys.path.insert(0, str(Path.cwd()))

    # Boot the application so config/queue.py (and the other config files)
    # configure the managers, and event listener allowlists are populated.
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

    # Graceful drain: on SIGTERM (systemd/Docker stop) finish the job in
    # flight, then exit instead of dying mid-job.
    _stop = {"requested": False}

    def _request_stop(signum, frame):
        _stop["requested"] = True
        click.echo("\n  SIGTERM received — finishing current job, then stopping.")

    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _request_stop)
        except (ValueError, OSError):
            pass  # not in the main thread

    click.echo(f"  Processing queue: {queue} (driver: {name}). Press Ctrl+C to stop.")
    try:
        while not _stop["requested"]:
            job_data = driver.pop(queue)
            if job_data is None:
                if once or _stop["requested"]:
                    break
                time.sleep(sleep)
                continue

            job_id = job_data["id"]
            attempts = job_data["attempts"]
            payload_str = job_data["payload"]
            payload: dict = {}
            job_queue = job_data.get("queue", queue)
            t0_job = time.monotonic()

            try:
                envelope = json.loads(payload_str)
                if "body" not in envelope or "signature" not in envelope:
                    raise ValueError("Job payload is missing signature envelope — rejecting unsigned payload.")
                from hunt.security.signing import verify as _verify

                if not _verify(envelope["body"], envelope["signature"]):
                    raise ValueError("Job payload signature mismatch — payload may have been tampered.")
                payload = json.loads(envelope["body"])

                from hunt.queue.job import Job as _Job

                cls = _safe_import(payload["class"], _Job)
                instance = cls(**payload.get("data", {}))
                job_timeout = int(payload.get("timeout", getattr(instance, "timeout", 60)))
                _run_with_timeout(instance.handle, job_timeout)
                duration_ms = int((time.monotonic() - t0_job) * 1000)
                driver.delete(job_id)
                _record_history(payload["class"], job_queue, duration_ms, "completed")
                click.echo(f"  Processed: {payload['class']}")

                # Dispatch chain continuation if present
                chain = payload.get("chain", [])
                if chain:
                    first = chain[0]
                    first["chain"] = chain[1:]
                    driver.push_payload(first, first.get("queue", queue))

            except Exception as exc:
                job_class = payload.get("class", "unknown")
                duration_ms = int((time.monotonic() - t0_job) * 1000)
                click.echo(f"  Failed: {job_class} — {exc}", err=True)

                job_tries = payload.get("tries", tries)
                if attempts >= job_tries:
                    try:
                        from hunt.queue.job import Job as _Job

                        cls = _safe_import(job_class, _Job)
                        instance = cls(**payload.get("data", {}))
                        instance.failed(exc)
                    except Exception:
                        pass
                    driver.fail(job_id, job_queue, payload_str, str(exc))
                    _record_history(job_class, job_queue, duration_ms, "failed")
                    click.echo(f"  Moved to failed after {attempts} attempt(s): {job_class}", err=True)
                else:
                    backoff = payload.get("backoff", 0)
                    delay = _backoff_delay(backoff, attempts) or 30
                    driver.release(job_id, delay=delay)

            if once:
                break

        if _stop["requested"]:
            click.echo("  Worker stopped.")

    except KeyboardInterrupt:
        click.echo("\n  Worker stopped.")
