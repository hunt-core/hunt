"""Regression tests for the scheduler (SCH-01 .. SCH-04)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from hunt.scheduling.cron import matches, validate
from hunt.scheduling.scheduler import ScheduledTask, Scheduler

REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")

# June 2024: the 10th is a Monday, the 16th a Sunday.
MON, TUE, WED, THU, FRI, SAT, SUN = (datetime(2024, 6, day, 9, 0) for day in (10, 11, 12, 13, 14, 15, 16))


# ---------------------------------------------------------------------------
# SCH-01: standard cron semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "when", "expected"),
    [
        # day-of-week numbering: 0 and 7 are Sunday, 1 is Monday
        ("0 9 * * 0", SUN, True),
        ("0 9 * * 0", MON, False),
        ("0 9 * * 7", SUN, True),
        ("0 9 * * 7", SAT, False),
        ("0 9 * * 1", MON, True),
        ("0 9 * * 1", TUE, False),
        ("0 9 * * 6", SAT, True),
        # weekdays
        ("0 9 * * 1-5", MON, True),
        ("0 9 * * 1-5", FRI, True),
        ("0 9 * * 1-5", SAT, False),
        ("0 9 * * 1-5", SUN, False),
        # weekend, in every spelling
        ("0 9 * * 6,0", SAT, True),
        ("0 9 * * 6,0", SUN, True),
        ("0 9 * * 6-7", SUN, True),
        ("0 9 * * 6-7", FRI, False),
        ("0 9 * * SAT,SUN", SUN, True),
        ("0 9 * * sat,sun", MON, False),
        ("0 9 * * FRI-SUN", SUN, True),
        ("0 9 * * FRI-SUN", THU, False),
        ("0 9 * * MON-FRI", WED, True),
        ("0 9 * * MON-FRI", SUN, False),
        ("0 9 * * */2", SUN, True),  # 0,2,4,6
        ("0 9 * * */2", MON, False),
        # month names
        ("0 9 * JUN *", MON, True),
        ("0 9 * jan,jul *", MON, False),
        ("0 9 * JAN-JUN *", MON, True),
        # n/step means "from n to the maximum"
        ("5/15 * * * *", datetime(2024, 6, 10, 9, 5), True),
        ("5/15 * * * *", datetime(2024, 6, 10, 9, 20), True),
        ("5/15 * * * *", datetime(2024, 6, 10, 9, 50), True),
        ("5/15 * * * *", datetime(2024, 6, 10, 9, 0), False),
        ("5/15 * * * *", datetime(2024, 6, 10, 9, 15), False),
        # steps on ranges and stars
        ("10-40/10 * * * *", datetime(2024, 6, 10, 9, 30), True),
        ("10-40/10 * * * *", datetime(2024, 6, 10, 9, 50), False),
        ("0 0 */2 * *", datetime(2024, 6, 1, 0, 0), True),
        ("0 0 */2 * *", datetime(2024, 6, 2, 0, 0), False),
        # day-of-month OR day-of-week when both are restricted
        ("0 9 13 * 5", THU, True),  # the 13th (a Thursday)
        ("0 9 13 * 5", FRI, True),  # a Friday (the 14th)
        ("0 9 13 * 5", WED, False),
        # ... but AND when either one is a star
        ("0 9 13 * *", THU, True),
        ("0 9 13 * *", FRI, False),
        ("0 9 * * 5", FRI, True),
        ("0 9 * * 5", THU, False),
        ("0 9 */2 * 5", FRI, False),  # the 14th is not an odd day; '*/2' counts as a star
        ("0 9 */2 * 5", datetime(2024, 6, 7, 9, 0), True),  # Friday the 7th
    ],
)
def test_cron_semantics(expression, when, expected):
    assert matches(expression, when) is expected


@pytest.mark.parametrize(
    "expression",
    [
        "*/0 * * * *",  # used to raise ZeroDivisionError
        "*/-5 * * * *",
        "*/x * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * 32 * *",
        "* * * 13 *",
        "* * * 0 *",
        "* * * * 8",
        "30-10 * * * *",  # backwards range
        "* * * * FOO",
        "* * * MON *",  # day name in the month field
        "a * * * *",
        "1,,2 * * * *",
        "* * * *",
        "* * * * * *",
        "",
    ],
)
def test_invalid_expressions_are_rejected(expression):
    with pytest.raises(ValueError):
        validate(expression)
    with pytest.raises(ValueError):
        matches(expression, MON)
    with pytest.raises(ValueError):
        ScheduledTask(lambda: None).cron(expression)


def test_expression_is_validated_at_registration():
    scheduler = Scheduler()
    with pytest.raises(ValueError, match="step"):
        scheduler.call(lambda: None).cron("*/0 * * * *")
    with pytest.raises(ValueError):
        scheduler.call(lambda: None).daily_at("25:00")
    with pytest.raises(ValueError):
        scheduler.call(lambda: None).weekly_on(9)


def test_weekly_is_sunday_and_weekly_on_uses_standard_numbers():
    weekly = ScheduledTask(lambda: None).weekly()
    assert weekly.is_due(datetime(2024, 6, 16, 0, 0))  # Sunday
    assert not weekly.is_due(datetime(2024, 6, 17, 0, 0))  # Monday

    monday = ScheduledTask(lambda: None).weekly_on(1, "8:30")
    assert monday.is_due(datetime(2024, 6, 10, 8, 30))
    assert not monday.is_due(datetime(2024, 6, 11, 8, 30))
    assert ScheduledTask(lambda: None).weekly_on(7).is_due(datetime(2024, 6, 16, 0, 0))
    assert ScheduledTask(lambda: None).weekly_on("FRI").is_due(datetime(2024, 6, 14, 0, 0))


def test_one_unusable_task_does_not_abort_due_tasks():
    scheduler = Scheduler()
    broken = scheduler.call(lambda: None)
    broken._expression = "*/0 * * * *"  # bypasses validation, as old code could
    good = scheduler.call(lambda: None).every_minute()
    assert scheduler.due_tasks(MON) == [good]


def test_timezone_evaluates_the_expression_in_that_zone():
    task = ScheduledTask(lambda: None).daily_at("9:00").timezone("America/New_York")
    # 13:00 UTC is 09:00 in New York during daylight saving time (June).
    assert task.is_due(datetime(2024, 6, 10, 13, 0, tzinfo=UTC))
    assert not task.is_due(datetime(2024, 6, 10, 9, 0, tzinfo=UTC))
    # ... and 14:00 UTC in winter.
    assert task.is_due(datetime(2024, 1, 10, 14, 0, tzinfo=UTC))
    assert task.summary()["timezone"] == "America/New_York"

    # The weekday is taken in the task's zone too: Monday 02:00 UTC is still Sunday in New York.
    sunday_evening = ScheduledTask(lambda: None).cron("0 22 * * 0").timezone("America/New_York")
    assert sunday_evening.is_due(datetime(2024, 6, 10, 2, 0, tzinfo=UTC))


def test_timezone_applies_to_between_window():
    ran = []
    task = ScheduledTask(lambda: ran.append(1)).between("8:00", "10:00").timezone("Asia/Tokyo")
    task.run(datetime(2024, 6, 10, 0, 0, tzinfo=UTC))  # 09:00 in Tokyo
    assert ran == [1]
    task.run(datetime(2024, 6, 10, 9, 0, tzinfo=UTC))  # 18:00 in Tokyo
    assert ran == [1]


def test_unknown_timezone_is_rejected_at_registration():
    with pytest.raises(Exception, match="Not/AZone"):
        ScheduledTask(lambda: None).timezone("Not/AZone")


# ---------------------------------------------------------------------------
# SCH-02: schedule:run
# ---------------------------------------------------------------------------


def _write_schedule(tmp_path: Path, body: str) -> None:
    console_dir = tmp_path / "app" / "console"
    console_dir.mkdir(parents=True)
    (console_dir / "schedule.py").write_text(textwrap.dedent(body))


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    saved_path = list(sys.path)
    saved_modules = set(sys.modules)
    yield tmp_path
    sys.path[:] = saved_path
    for name in set(sys.modules) - saved_modules:
        if name == "bootstrap" or name.startswith("bootstrap."):
            del sys.modules[name]


def test_failing_task_does_not_stop_later_tasks(project):
    from hunt.console.commands.schedule_run import schedule_run_command

    _write_schedule(
        project,
        """
        from pathlib import Path

        def explode():
            raise RuntimeError("first task broke")

        def schedule(s):
            s.call(explode, description="broken").every_minute()
            s.call(lambda: Path("second.txt").write_text("ran"), description="healthy").every_minute()
        """,
    )
    result = CliRunner().invoke(schedule_run_command, [])
    assert (project / "second.txt").read_text() == "ran"
    assert result.exit_code == 1
    assert "first task broke" in result.output
    assert "1 scheduled task(s) failed" in result.output


def test_exit_code_is_zero_when_all_tasks_pass(project):
    from hunt.console.commands.schedule_run import schedule_run_command

    _write_schedule(
        project,
        """
        def schedule(s):
            s.call(lambda: None).every_minute()
        """,
    )
    assert CliRunner().invoke(schedule_run_command, []).exit_code == 0


def test_background_task_finishes_before_the_command_returns(project):
    from hunt.console.commands.schedule_run import schedule_run_command

    _write_schedule(
        project,
        """
        import time
        from pathlib import Path

        def slow():
            time.sleep(0.3)
            Path("background.txt").write_text("done")

        def schedule(s):
            s.call(slow, description="slow").every_minute().run_in_background()
        """,
    )
    result = CliRunner().invoke(schedule_run_command, [])
    assert result.exit_code == 0, result.output
    # Written after a sleep on another thread: only there if the command waited.
    assert (project / "background.txt").read_text() == "done"


def test_failing_background_task_sets_the_exit_code(project):
    from hunt.console.commands.schedule_run import schedule_run_command

    _write_schedule(
        project,
        """
        def explode():
            raise RuntimeError("background broke")

        def schedule(s):
            s.call(explode, description="bg").every_minute().run_in_background()
        """,
    )
    result = CliRunner().invoke(schedule_run_command, [])
    assert result.exit_code == 1
    assert "background broke" in result.output


def test_background_thread_is_not_a_daemon():
    release = threading.Event()
    task = ScheduledTask(release.wait).run_in_background()
    thread = task.run()
    try:
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is False
    finally:
        release.set()
        thread.join(5)


def test_schedule_run_boots_the_application(project):
    """The app is booted (bootstrap/app.py) before the schedule file is loaded."""
    from hunt.console.commands.schedule_run import schedule_run_command

    (project / "bootstrap").mkdir()
    (project / "bootstrap" / "__init__.py").write_text("")
    (project / "bootstrap" / "app.py").write_text("BOOTED = True\n")
    _write_schedule(
        project,
        """
        import sys
        from pathlib import Path

        def schedule(s):
            booted = getattr(sys.modules.get("bootstrap.app"), "BOOTED", False)
            Path("boot.txt").write_text(str(booted))
        """,
    )
    result = CliRunner().invoke(schedule_run_command, [])
    assert result.exit_code == 0, result.output
    assert (project / "boot.txt").read_text() == "True"


def test_schedule_run_loads_dotenv_without_an_app(project, monkeypatch):
    from hunt.console.commands.schedule_run import schedule_run_command

    monkeypatch.delenv("HUNT_SCHEDULE_PROBE", raising=False)
    (project / ".env").write_text("HUNT_SCHEDULE_PROBE=from-dotenv\n")
    _write_schedule(
        project,
        """
        import os
        from pathlib import Path

        def schedule(s):
            Path("env.txt").write_text(os.environ.get("HUNT_SCHEDULE_PROBE", "missing"))
        """,
    )
    try:
        result = CliRunner().invoke(schedule_run_command, [])
        assert result.exit_code == 0, result.output
        assert (project / "env.txt").read_text() == "from-dotenv"
    finally:
        import os

        os.environ.pop("HUNT_SCHEDULE_PROBE", None)


# ---------------------------------------------------------------------------
# SCH-03: scheduler.command()
# ---------------------------------------------------------------------------


def test_python_dash_m_hunt_works():
    result = subprocess.run([sys.executable, "-m", "hunt", "--version"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    assert result.stdout.startswith("hunt, version")


def test_command_uses_the_current_interpreter(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("hunt.scheduling.scheduler.subprocess.run", fake_run)
    task = Scheduler().command("cache:clear", "--force")
    assert task.run() == 0
    assert calls[0][0] == [sys.executable, "-m", "hunt", "cache:clear", "--force"]
    assert "shell" not in calls[0][1]


def test_command_output_is_captured_to_the_file(tmp_path):
    out = tmp_path / "logs" / "version.log"
    succeeded = []
    task = Scheduler().command("--version").send_output_to(str(out)).on_success(lambda: succeeded.append(1))
    task.run()
    assert out.read_text().startswith("hunt, version")
    assert succeeded == [1]

    task = Scheduler().command("--version").append_output_to(str(out))
    task.run()
    assert out.read_text().count("hunt, version") == 2


def test_command_failure_is_a_task_failure(tmp_path):
    out = tmp_path / "fail.log"
    outcome = []
    task = (
        Scheduler()
        .command("definitely-not-a-command")
        .send_output_to(str(out))
        .on_success(lambda: outcome.append("success"))
        .on_failure(lambda: outcome.append("failure"))
    )
    with pytest.raises(RuntimeError, match="exited with status 2"):
        task.run()
    assert outcome == ["failure"]
    assert "No such command" in out.read_text()  # stderr is captured too


def test_callable_output_capture_still_works(tmp_path):
    out = tmp_path / "call.log"
    ScheduledTask(lambda: print("hello from task")).send_output_to(str(out)).run()
    assert out.read_text() == "hello from task\n"


# ---------------------------------------------------------------------------
# SCH-04: without_overlapping lock key
# ---------------------------------------------------------------------------


def _build_schedule() -> Scheduler:
    scheduler = Scheduler()
    scheduler.call(lambda: None, description="reports").hourly().without_overlapping()
    scheduler.call(lambda: None).hourly().without_overlapping()
    scheduler.call(lambda: None).hourly().without_overlapping()
    scheduler.call(lambda: None).name("nightly/cleanup job").daily()
    return scheduler


def test_lock_key_is_stable_and_distinct():
    first, second = _build_schedule().tasks, _build_schedule().tasks
    assert [t.lock_key() for t in first] == [t.lock_key() for t in second]  # same in a fresh "process"
    assert len({t.lock_key() for t in first}) == 4  # anonymous twins do not share a lock
    assert first[3].lock_key() == "nightly_cleanup_job"  # named, and safe as a file name
    assert str(id(first[0])) not in first[0].lock_key()


def test_lock_key_is_identical_in_another_process():
    script = textwrap.dedent(
        """
        from hunt.scheduling.scheduler import Scheduler
        s = Scheduler()
        s.call(print, description="reports").hourly().without_overlapping()
        print(s.tasks[0].lock_key())
        """
    )
    keys = set()
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=_env_with_src()
        )
        assert result.returncode == 0, result.stderr
        keys.add(result.stdout.strip())
    scheduler = Scheduler()
    scheduler.call(print, description="reports").hourly().without_overlapping()
    keys.add(scheduler.tasks[0].lock_key())
    assert len(keys) == 1


def _env_with_src() -> dict:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_SRC + os.pathsep + env.get("PYTHONPATH", "")
    return env


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX file locks")
def test_unnamed_task_does_not_overlap_across_processes(project):
    """A second schedule:run process must see the lock held by the first (no .name() set)."""
    from hunt.console.commands.schedule_run import _load_schedule

    _write_schedule(
        project,
        """
        import time
        from pathlib import Path

        def work():
            Path("runs.txt").open("a").write("x")
            Path("holding").write_text("1")
            deadline = time.time() + 30
            while not Path("release").exists() and time.time() < deadline:
                time.sleep(0.02)

        def schedule(s):
            s.call(work).every_minute().without_overlapping()
        """,
    )
    holder = textwrap.dedent(
        """
        from hunt.console.commands.schedule_run import _load_schedule
        from hunt.scheduling.scheduler import Scheduler

        s = Scheduler()
        _load_schedule(s)
        s.tasks[0].run()
        """
    )
    process = subprocess.Popen([sys.executable, "-c", holder], cwd=project, env=_env_with_src())
    try:
        deadline = time.time() + 30
        while not (project / "holding").exists():
            assert process.poll() is None, "holder process exited early"
            assert time.time() < deadline, "holder process never took the lock"
            time.sleep(0.02)

        scheduler = Scheduler()
        _load_schedule(scheduler)
        task = scheduler.tasks[0]
        assert task._name == ""
        task.run()  # must be skipped: the other process holds the lock
        assert (project / "runs.txt").read_text() == "x"
    finally:
        (project / "release").write_text("1")
        process.wait(timeout=30)

    task.run()  # lock released once the other process finished
    assert (project / "runs.txt").read_text() == "xx"
    locks = list((project / "storage" / "framework" / "schedule").glob("*.lock"))
    assert len(locks) == 1  # one stable file, not one per process
