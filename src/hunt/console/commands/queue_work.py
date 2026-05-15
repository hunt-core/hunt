from __future__ import annotations

import importlib
import json
import re
import sys
import time
from pathlib import Path

import click

_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")


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
def queue_work_command(queue: str, sleep: int, tries: int, once: bool) -> None:
    """Process queued jobs."""
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    sys.path.insert(0, str(Path.cwd()))

    from hunt.queue.drivers.database import DatabaseDriver

    driver = DatabaseDriver()

    # Warn if queued-event allowlists are empty — QueuedEventListener jobs will
    # be rejected at runtime unless the app's EventServiceProvider is booted.
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

    click.echo(f"  Processing queue: {queue}. Press Ctrl+C to stop.")
    try:
        while True:
            job_data = driver.pop(queue)
            if job_data is None:
                if once:
                    break
                time.sleep(sleep)
                continue

            job_id = job_data["id"]
            attempts = job_data["attempts"]
            payload_str = job_data["payload"]
            payload: dict = {}

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
                instance.handle()
                driver.delete(job_id)
                click.echo(f"  Processed: {payload['class']}")

                # Dispatch chain continuation if present
                chain = payload.get("chain", [])
                if chain:
                    first = chain[0]
                    first["chain"] = chain[1:]
                    driver.push_payload(first, first.get("queue", queue))

            except Exception as exc:
                job_class = payload.get("class", "unknown")
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
                    driver.fail(job_id, job_data.get("queue", queue), payload_str, str(exc))
                    click.echo(f"  Moved to failed after {attempts} attempt(s): {job_class}", err=True)
                else:
                    backoff = payload.get("backoff", 0)
                    delay = _backoff_delay(backoff, attempts) or 30
                    driver.release(job_id, delay=delay)

            if once:
                break

    except KeyboardInterrupt:
        click.echo("\n  Worker stopped.")
