"""Tests for hunt.admin.controllers.schedule."""

from __future__ import annotations

from datetime import datetime

from hunt.admin.controllers.schedule import _load_scheduler, _next_run
from hunt.scheduling.scheduler import Scheduler

# ---------------------------------------------------------------------------
# _next_run
# ---------------------------------------------------------------------------


class TestNextRun:
    def test_every_minute_finds_next(self):
        after = datetime(2024, 6, 15, 12, 30)
        result = _next_run("* * * * *", after)
        assert result == "2024-06-15 12:31"

    def test_daily_at_midnight_finds_next(self):
        after = datetime(2024, 6, 15, 12, 0)
        result = _next_run("0 0 * * *", after)
        assert result == "2024-06-16 00:00"

    def test_invalid_expression_returns_dash(self):
        result = _next_run("not valid cron", datetime.now())
        assert result == "—"

    def test_wrong_field_count_returns_dash(self):
        result = _next_run("* * * *", datetime.now())
        assert result == "—"

    def test_hourly_finds_next(self):
        after = datetime(2024, 6, 15, 12, 5)
        result = _next_run("0 * * * *", after)
        assert result == "2024-06-15 13:00"

    def test_weekly_finds_next(self):
        # "0 0 * * 0" — Mondays at midnight (weekday() == 0)
        monday = datetime(2024, 6, 17, 0, 0)  # known Monday
        after = datetime(2024, 6, 15, 12, 0)
        result = _next_run("0 0 * * 0", after)
        assert result == monday.strftime("%Y-%m-%d %H:%M")

    def test_far_future_returns_dash(self):
        # expression that will never match in 8 days
        # "0 0 29 2 *" — Feb 29 midnight — won't occur in any 8-day window
        result = _next_run("0 0 29 2 *", datetime(2024, 6, 15, 0, 0))
        assert result == "—"

    def test_returns_string_format(self):
        result = _next_run("*/5 * * * *", datetime(2024, 6, 15, 12, 0))
        # Should be YYYY-MM-DD HH:MM
        assert len(result) == 16 or result == "—"

    def test_uses_now_when_after_is_none(self):
        result = _next_run("* * * * *")
        assert result != "—"
        assert len(result) == 16


# ---------------------------------------------------------------------------
# _load_scheduler
# ---------------------------------------------------------------------------


class TestLoadScheduler:
    def test_returns_empty_scheduler_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        scheduler = _load_scheduler()
        assert isinstance(scheduler, Scheduler)
        assert scheduler.tasks == []

    def test_loads_tasks_from_schedule_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling.scheduler import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None, description='Test task').every_minute()\n"
        )
        scheduler = _load_scheduler()
        assert len(scheduler.tasks) == 1
        assert scheduler.tasks[0].summary()["description"] == "Test task"

    def test_file_without_schedule_function_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text("# no schedule function here\nx = 42\n")
        scheduler = _load_scheduler()
        assert scheduler.tasks == []

    def test_multiple_tasks_loaded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling.scheduler import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None, description='A').daily()\n"
            "    s.call(lambda: None, description='B').hourly()\n"
            "    s.call(lambda: None, description='C').every_minute()\n"
        )
        scheduler = _load_scheduler()
        assert len(scheduler.tasks) == 3
