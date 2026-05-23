"""Tests for the task scheduler."""
from __future__ import annotations

from datetime import datetime

import pytest

from hunt.scheduling.cron import matches
from hunt.scheduling.scheduler import ScheduledTask, Scheduler

# ---------------------------------------------------------------------------
# cron.matches
# ---------------------------------------------------------------------------

class TestCronMatches:
    def test_every_minute_always_matches(self):
        assert matches("* * * * *", datetime(2024, 6, 15, 12, 30))

    def test_specific_minute(self):
        assert matches("30 * * * *", datetime(2024, 6, 15, 12, 30))
        assert not matches("30 * * * *", datetime(2024, 6, 15, 12, 31))

    def test_specific_hour(self):
        assert matches("0 9 * * *", datetime(2024, 6, 15, 9, 0))
        assert not matches("0 9 * * *", datetime(2024, 6, 15, 10, 0))

    def test_step_syntax(self):
        assert matches("*/15 * * * *", datetime(2024, 6, 15, 12, 0))
        assert matches("*/15 * * * *", datetime(2024, 6, 15, 12, 15))
        assert matches("*/15 * * * *", datetime(2024, 6, 15, 12, 30))
        assert matches("*/15 * * * *", datetime(2024, 6, 15, 12, 45))
        assert not matches("*/15 * * * *", datetime(2024, 6, 15, 12, 7))

    def test_range_syntax(self):
        assert matches("0 9-17 * * *", datetime(2024, 6, 15, 9, 0))
        assert matches("0 9-17 * * *", datetime(2024, 6, 15, 17, 0))
        assert not matches("0 9-17 * * *", datetime(2024, 6, 15, 18, 0))

    def test_list_syntax(self):
        assert matches("0 9,12,18 * * *", datetime(2024, 6, 15, 9, 0))
        assert matches("0 9,12,18 * * *", datetime(2024, 6, 15, 12, 0))
        assert not matches("0 9,12,18 * * *", datetime(2024, 6, 15, 10, 0))

    def test_dom_and_month(self):
        assert matches("0 0 1 1 *", datetime(2024, 1, 1, 0, 0))
        assert not matches("0 0 1 1 *", datetime(2024, 1, 2, 0, 0))
        assert not matches("0 0 1 1 *", datetime(2024, 2, 1, 0, 0))

    def test_day_of_week(self):
        # datetime.weekday(): 0=Mon … 6=Sun
        monday = datetime(2024, 6, 17, 0, 0)  # a known Monday
        assert matches("0 0 * * 0", monday)
        assert not matches("0 0 * * 1", monday)

    def test_invalid_expression_raises(self):
        with pytest.raises(ValueError):
            matches("* * * *")  # only 4 fields

    def test_range_with_step(self):
        assert matches("0 8-18/2 * * *", datetime(2024, 6, 15, 8, 0))
        assert matches("0 8-18/2 * * *", datetime(2024, 6, 15, 10, 0))
        assert not matches("0 8-18/2 * * *", datetime(2024, 6, 15, 9, 0))


# ---------------------------------------------------------------------------
# ScheduledTask fluent builders
# ---------------------------------------------------------------------------

class TestScheduledTask:
    def _task(self) -> ScheduledTask:
        return ScheduledTask(lambda: None)

    def test_every_minute(self):
        assert self._task().every_minute()._expression == "* * * * *"

    def test_every_five_minutes(self):
        assert self._task().every_five_minutes()._expression == "*/5 * * * *"

    def test_every_fifteen_minutes(self):
        assert self._task().every_fifteen_minutes()._expression == "*/15 * * * *"

    def test_every_thirty_minutes(self):
        assert self._task().every_thirty_minutes()._expression == "*/30 * * * *"

    def test_hourly(self):
        assert self._task().hourly()._expression == "0 * * * *"

    def test_hourly_at(self):
        assert self._task().hourly_at(30)._expression == "30 * * * *"

    def test_daily(self):
        assert self._task().daily()._expression == "0 0 * * *"

    def test_daily_at(self):
        assert self._task().daily_at("09:30")._expression == "30 09 * * *"

    def test_daily_at_hour_only(self):
        assert self._task().daily_at("9:")._expression == "0 9 * * *"

    def test_weekly(self):
        assert self._task().weekly()._expression == "0 0 * * 0"

    def test_monthly(self):
        assert self._task().monthly()._expression == "0 0 1 * *"

    def test_yearly(self):
        assert self._task().yearly()._expression == "0 0 1 1 *"

    def test_cron_custom(self):
        assert self._task().cron("5 4 * * 0")._expression == "5 4 * * 0"

    def test_description(self):
        t = self._task().description("Send newsletter")
        assert t.summary()["description"] == "Send newsletter"

    def test_name(self):
        t = self._task().name("newsletter")
        assert t.summary()["name"] == "newsletter"

    def test_is_due(self):
        t = self._task().daily()  # "0 0 * * *"
        assert t.is_due(datetime(2024, 6, 15, 0, 0))
        assert not t.is_due(datetime(2024, 6, 15, 0, 1))

    def test_run_calls_callback(self):
        called = []
        t = ScheduledTask(lambda: called.append(1))
        t.run()
        assert called == [1]

    def test_run_returns_callback_value(self):
        t = ScheduledTask(lambda: 42)
        assert t.run() == 42

    def test_fluent_chain_returns_self(self):
        t = self._task()
        assert t.daily() is t
        assert t.description("x") is t
        assert t.name("y") is t


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class TestScheduler:
    def test_call_registers_task(self):
        s = Scheduler()
        s.call(lambda: None)
        assert len(s.tasks) == 1

    def test_due_tasks_returns_due(self):
        s = Scheduler()
        s.call(lambda: None).every_minute()
        s.call(lambda: None).daily()  # "0 0 * * *"

        noon = datetime(2024, 6, 15, 12, 0)
        due = s.due_tasks(noon)
        assert len(due) == 1  # only every_minute is due at noon

    def test_due_tasks_empty_when_none_due(self):
        s = Scheduler()
        s.call(lambda: None).daily()  # only at midnight
        due = s.due_tasks(datetime(2024, 6, 15, 12, 1))
        assert due == []

    def test_all_tasks_due_at_midnight(self):
        s = Scheduler()
        s.call(lambda: None).every_minute()
        s.call(lambda: None).daily()
        midnight = datetime(2024, 6, 15, 0, 0)
        assert len(s.due_tasks(midnight)) == 2

    def test_command_registers_task(self):
        s = Scheduler()
        t = s.command("db:seed")
        assert t in s.tasks

    def test_description_passed_to_task(self):
        s = Scheduler()
        t = s.call(lambda: None, description="test task")
        assert t.summary()["description"] == "test task"


# ---------------------------------------------------------------------------
# schedule:run command
# ---------------------------------------------------------------------------

class TestScheduleRunCommand:
    def test_runs_due_tasks(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_run import schedule_run_command

        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        # Write a schedule.py that registers a task
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None).every_minute()\n"
        )

        runner = CliRunner()
        result = runner.invoke(schedule_run_command, [])
        assert result.exit_code == 0

    def test_no_tasks_due_prints_nothing(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_run import schedule_run_command

        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None).yearly()  # won't be due right now unless Jan 1 00:00\n"
        )

        runner = CliRunner()
        result = runner.invoke(schedule_run_command, [])
        assert result.exit_code == 0

    def test_missing_schedule_file_is_ok(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_run import schedule_run_command

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(schedule_run_command, [])
        assert result.exit_code == 0

    def test_verbose_shows_task_label(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_run import schedule_run_command

        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None, description='My task').every_minute()\n"
        )

        runner = CliRunner()
        result = runner.invoke(schedule_run_command, ["--verbose"])
        assert result.exit_code == 0
        assert "My task" in result.output


# ---------------------------------------------------------------------------
# schedule:list command
# ---------------------------------------------------------------------------

class TestScheduleListCommand:
    def test_lists_tasks(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_list import schedule_list_command

        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None, description='Send email').daily()\n"
        )

        runner = CliRunner()
        result = runner.invoke(schedule_list_command, [])
        assert result.exit_code == 0
        assert "0 0 * * *" in result.output
        assert "Send email" in result.output

    def test_no_tasks_message(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_list import schedule_list_command

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(schedule_list_command, [])
        assert result.exit_code == 0
        assert "No tasks" in result.output

    def test_multiple_tasks_listed(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.schedule_list import schedule_list_command

        monkeypatch.chdir(tmp_path)
        console_dir = tmp_path / "app" / "console"
        console_dir.mkdir(parents=True)
        (console_dir / "schedule.py").write_text(
            "from hunt.scheduling import Scheduler\n"
            "def schedule(s: Scheduler):\n"
            "    s.call(lambda: None, description='Task A').daily()\n"
            "    s.call(lambda: None, description='Task B').hourly()\n"
        )

        runner = CliRunner()
        result = runner.invoke(schedule_list_command, [])
        assert "Task A" in result.output
        assert "Task B" in result.output
