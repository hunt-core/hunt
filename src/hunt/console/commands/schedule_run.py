from __future__ import annotations

import importlib.util
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
    """Run all scheduled tasks that are due right now."""
    scheduler = Scheduler()
    _load_schedule(scheduler)

    due = scheduler.due_tasks()
    if not due:
        if verbose:
            click.echo("  No tasks due.")
        return

    for task in due:
        summary = task.summary()
        if verbose:
            label = summary["description"] or summary["name"]
            click.echo(f"  Running: {label}")
        task.run()
