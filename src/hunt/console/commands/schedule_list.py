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


@click.command("schedule:list")
def schedule_list_command() -> None:
    """List all scheduled tasks."""
    scheduler = Scheduler()
    _load_schedule(scheduler)

    tasks = scheduler.tasks
    if not tasks:
        click.echo("  No tasks scheduled.")
        return

    col_expr = max(len(t.summary()["expression"]) for t in tasks)
    col_expr = max(col_expr, 10)

    header = f"  {'Expression':<{col_expr}}  Description"
    click.echo(header)
    click.echo("  " + "-" * (len(header) - 2))

    for task in tasks:
        s = task.summary()
        click.echo(f"  {s['expression']:<{col_expr}}  {s['description']}")
