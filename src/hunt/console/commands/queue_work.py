from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import click


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
            payload: dict = {}

            try:
                envelope = json.loads(job_data["payload"])
                if "body" in envelope and "signature" in envelope:
                    from hunt.security.signing import verify as _verify
                    if not _verify(envelope["body"], envelope["signature"]):
                        raise ValueError("Job payload signature mismatch — payload may have been tampered.")
                    payload = json.loads(envelope["body"])
                else:
                    payload = envelope  # legacy unsigned payload

                from hunt.queue.job import Job as _Job
                module_path, class_name = payload["class"].rsplit(".", 1)
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                if not (isinstance(cls, type) and issubclass(cls, _Job)):
                    raise TypeError(f"Refusing to execute non-Job class: {payload['class']!r}")
                instance = cls(**payload.get("data", {}))
                instance.handle()
                driver.delete(job_id)
                click.echo(f"  Processed: {payload['class']}")
            except Exception as exc:
                job_class = payload.get("class", "unknown")
                click.echo(f"  Failed: {job_class} — {exc}", err=True)
                if attempts >= tries:
                    driver.delete(job_id)
                    click.echo(f"  Discarded after {attempts} attempts: {job_class}", err=True)
                else:
                    driver.release(job_id, delay=30)

            if once:
                break

    except KeyboardInterrupt:
        click.echo("\n  Worker stopped.")
