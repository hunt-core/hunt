from __future__ import annotations

import importlib.util
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from hunt.http.request import Request
from hunt.http.response import Response
from hunt.scheduling.cron import matches
from hunt.scheduling.scheduler import Scheduler

_log = logging.getLogger(__name__)

# How far ahead to look for the next run. Four years plus a leap day covers
# every valid expression, including "0 0 29 2 *".
_NEXT_RUN_HORIZON_DAYS = 366 * 4 + 1

_cache_lock = threading.Lock()
# (path, mtime_ns, size) -> Scheduler built from that version of the file.
_scheduler_cache: tuple[tuple[str, int, int], Scheduler] | None = None


def _schedule_file() -> Path:
    return Path.cwd() / "app" / "console" / "schedule.py"


def _load_scheduler() -> Scheduler:
    """Build the scheduler from app/console/schedule.py.

    The module is executed once per version of the file (keyed on path, mtime
    and size) instead of on every page view, so its import-time side effects
    are not repeated by simply opening the Schedule page.
    """
    global _scheduler_cache
    schedule_file = _schedule_file()
    if not schedule_file.exists():
        return Scheduler()
    try:
        stat = schedule_file.stat()
        key = (str(schedule_file), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return Scheduler()

    with _cache_lock:
        if _scheduler_cache is not None and _scheduler_cache[0] == key:
            return _scheduler_cache[1]
        scheduler = Scheduler()
        spec = importlib.util.spec_from_file_location("app.console.schedule", schedule_file)
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        if hasattr(module, "schedule"):
            module.schedule(scheduler)
        _scheduler_cache = (key, scheduler)
        return scheduler


def _next_run(expression: str, after: datetime | None = None) -> str:
    """Find the next datetime matching the cron expression.

    Works field by field instead of minute by minute: first the next matching
    day (one check per day), then the hour within it, then the minute. That
    keeps the search cheap enough to look years ahead, so monthly and yearly
    schedules get a real answer rather than a dash.
    """
    fields = str(expression).split()
    if len(fields) != 5:
        return "—"
    minute_f, hour_f, dom_f, month_f, dow_f = fields
    day_expr = f"* * {dom_f} {month_f} {dow_f}"
    hour_expr = f"* {hour_f} * * *"
    minute_expr = f"{minute_f} * * * *"

    start = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    try:
        day = start.replace(hour=0, minute=0)
        for _ in range(_NEXT_RUN_HORIZON_DAYS):
            if matches(day_expr, day):
                first_hour = start.hour if day.date() == start.date() else 0
                for hour in range(first_hour, 24):
                    candidate_hour = day.replace(hour=hour)
                    if not matches(hour_expr, candidate_hour):
                        continue
                    same_hour = day.date() == start.date() and hour == start.hour
                    first_minute = start.minute if same_hour else 0
                    for minute in range(first_minute, 60):
                        candidate = candidate_hour.replace(minute=minute)
                        if matches(minute_expr, candidate):
                            return candidate.strftime("%Y-%m-%d %H:%M")
            day += timedelta(days=1)
    except Exception:
        return "—"
    return "—"


def _next_run_for(task: Any, summary: dict, now: datetime) -> str:
    timezone = summary.get("timezone") or ""
    local_time = getattr(task, "_local_time", None)
    after = local_time(now) if timezone and callable(local_time) else now
    result = _next_run(summary["expression"], after)
    return f"{result} {timezone}" if timezone and result != "—" else result


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    Admin.require_tool(request, "schedule")
    load_error = False
    try:
        scheduler = _load_scheduler()
    except Exception:
        _log.exception("Admin schedule: could not load app/console/schedule.py")
        scheduler = Scheduler()
        load_error = True
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
                # Evaluate in the task's own timezone (when it has one), as the
                # scheduler itself does, and label the result with it.
                "next_run": _next_run_for(task, s, now),
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
            "missing_schedule_file": not _schedule_file().exists(),
            "schedule_load_error": load_error,
        }
    )
    return Admin._render("admin/schedule/index.html", ctx)
