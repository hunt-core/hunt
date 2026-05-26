from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

from hunt.http.request import Request
from hunt.http.response import Response
from hunt.scheduling.cron import matches
from hunt.scheduling.scheduler import Scheduler


def _load_scheduler() -> Scheduler:
    scheduler = Scheduler()
    schedule_file = Path.cwd() / "app" / "console" / "schedule.py"
    if not schedule_file.exists():
        return scheduler
    spec = importlib.util.spec_from_file_location("app.console.schedule", schedule_file)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    if hasattr(module, "schedule"):
        module.schedule(scheduler)
    return scheduler


def _next_run(expression: str, after: datetime | None = None) -> str:
    """Find the next datetime matching the cron expression (scans up to 8 days ahead)."""
    dt = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = dt + timedelta(days=8)
    while dt < limit:
        try:
            if matches(expression, dt):
                return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return "—"
        dt += timedelta(minutes=1)
    return "—"


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    scheduler = _load_scheduler()
    now = datetime.now()

    tasks = []
    for task in scheduler.tasks:
        s = task.summary()
        due_now = task.is_due(now)
        tasks.append(
            {
                "expression": s["expression"],
                "description": s["description"],
                "name": s["name"],
                "next_run": _next_run(s["expression"], now),
                "due_now": due_now,
                "environments": task._environments,
                "without_overlapping": task._without_overlapping,
                "background": task._run_in_background,
            }
        )

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": "Schedule",
            "tasks": tasks,
            "missing_schedule_file": not (Path.cwd() / "app" / "console" / "schedule.py").exists(),
        }
    )
    return Admin._render("admin/schedule/index.html", ctx)
