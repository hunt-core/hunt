from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import click

from hunt.scheduling.scheduler import Scheduler


def _load_schedule(scheduler: Scheduler) -> None:
    schedule_file = Path.cwd() / "app" / "console" / "schedule.py"
    if not schedule_file.exists():
        return
    spec = importlib.util.spec_from_file_location("app.console.schedule", schedule_file)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if hasattr(module, "schedule"):
        module.schedule(scheduler)


@click.command("schedule:run")
@click.option("--verbose", "-v", is_flag=True, help="Show each task as it runs")
def schedule_run_command(verbose: bool) -> None:
    """Run all scheduled tasks that are due right now.

    Exits with status 1 if any task failed.
    """
    # Boot the application (config, database connection, providers) the same
    # way queue:work does, so tasks do not run against unconfigured defaults.
    from hunt.console.commands.queue_work import boot_application

    boot_application()

    scheduler = Scheduler()
    _load_schedule(scheduler)

    due = scheduler.due_tasks()
    if not due:
        if verbose:
            click.echo("  No tasks due.")
        return

    failed: list[str] = []
    background: list[tuple[str, object, threading.Thread]] = []

    for task in due:
        summary = task.summary()
        label = summary["description"] or summary["name"]
        if verbose:
            click.echo(f"  Running: {label}")
        try:
            result = task.run()
        except Exception as exc:
            # A failing task must not stop the tasks after it.
            failed.append(label)
            click.echo(f"  Failed: {label} — {exc}", err=True)
            continue
        if isinstance(result, threading.Thread):
            background.append((label, task, result))

    # Background tasks keep running in threads; wait for them so they are not
    # cut off when this command returns.
    for label, task, thread in background:
        thread.join()
        error = getattr(task, "_background_error", None)
        if error is not None:
            failed.append(label)
            click.echo(f"  Failed: {label} — {error}", err=True)

    if failed:
        click.echo(f"  {len(failed)} scheduled task(s) failed.", err=True)
        raise SystemExit(1)
