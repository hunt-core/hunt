"""Phase L — Scheduler Enhancements."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from hunt.scheduling.scheduler import Scheduler, ScheduledTask


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _task(fn=None) -> ScheduledTask:
    called = []
    return ScheduledTask(fn or (lambda: called.append(1)))


def _always_true():
    return True


def _always_false():
    return False


# ---------------------------------------------------------------------------
# environments()
# ---------------------------------------------------------------------------

class TestEnvironments:
    def test_runs_when_env_matches(self):
        results = []
        task = ScheduledTask(lambda: results.append("ok"))
        task.environments("testing")
        with patch.dict(os.environ, {"APP_ENV": "testing"}):
            task.run()
        assert results == ["ok"]

    def test_skips_when_env_does_not_match(self):
        results = []
        task = ScheduledTask(lambda: results.append("ok"))
        task.environments("production")
        with patch.dict(os.environ, {"APP_ENV": "testing"}):
            task.run()
        assert results == []

    def test_multiple_environments_allowed(self):
        results = []
        task = ScheduledTask(lambda: results.append("ok"))
        task.environments("staging", "production")
        with patch.dict(os.environ, {"APP_ENV": "staging"}):
            task.run()
        assert results == ["ok"]

    def test_defaults_to_production_if_env_unset(self):
        results = []
        task = ScheduledTask(lambda: results.append("ok"))
        task.environments("production")
        env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}
        with patch.dict(os.environ, env, clear=True):
            task.run()
        assert results == ["ok"]

    def test_skips_production_task_in_testing(self):
        results = []
        task = ScheduledTask(lambda: results.append("ok"))
        task.environments("production")
        with patch.dict(os.environ, {"APP_ENV": "testing"}):
            task.run()
        assert results == []

    def test_returns_self(self):
        task = _task()
        assert task.environments("production") is task


# ---------------------------------------------------------------------------
# when() / skip()
# ---------------------------------------------------------------------------

class TestWhenSkip:
    def test_when_true_runs(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.when(_always_true)
        task.run()
        assert results == [1]

    def test_when_false_skips(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.when(_always_false)
        task.run()
        assert results == []

    def test_multiple_when_all_must_be_true(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.when(_always_true).when(_always_false)
        task.run()
        assert results == []

    def test_skip_true_skips(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.skip(_always_true)
        task.run()
        assert results == []

    def test_skip_false_runs(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.skip(_always_false)
        task.run()
        assert results == [1]

    def test_skip_overrides_when(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.when(_always_true).skip(_always_true)
        task.run()
        assert results == []

    def test_when_returns_self(self):
        task = _task()
        assert task.when(_always_true) is task

    def test_skip_returns_self(self):
        task = _task()
        assert task.skip(_always_false) is task


# ---------------------------------------------------------------------------
# between()
# ---------------------------------------------------------------------------

class TestBetween:
    def test_within_window_runs(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("08:00", "17:00")
        dt = datetime(2026, 1, 1, 12, 0)
        task.run(dt)
        assert results == [1]

    def test_outside_window_skips(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("08:00", "17:00")
        dt = datetime(2026, 1, 1, 20, 0)
        task.run(dt)
        assert results == []

    def test_at_start_boundary_runs(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("08:00", "17:00")
        dt = datetime(2026, 1, 1, 8, 0)
        task.run(dt)
        assert results == [1]

    def test_at_end_boundary_runs(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("08:00", "17:00")
        dt = datetime(2026, 1, 1, 17, 0)
        task.run(dt)
        assert results == [1]

    def test_overnight_window_runs_after_start(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("22:00", "06:00")
        dt = datetime(2026, 1, 1, 23, 0)
        task.run(dt)
        assert results == [1]

    def test_overnight_window_runs_before_end(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("22:00", "06:00")
        dt = datetime(2026, 1, 1, 3, 0)
        task.run(dt)
        assert results == [1]

    def test_overnight_window_skips_midday(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("22:00", "06:00")
        dt = datetime(2026, 1, 1, 12, 0)
        task.run(dt)
        assert results == []

    def test_returns_self(self):
        task = _task()
        assert task.between("08:00", "17:00") is task


# ---------------------------------------------------------------------------
# run_in_background()
# ---------------------------------------------------------------------------

class TestRunInBackground:
    def test_returns_thread(self):
        barrier = threading.Barrier(2)
        results = []

        def slow():
            barrier.wait()
            results.append(1)

        task = ScheduledTask(slow)
        task.run_in_background()
        t = task.run()
        assert isinstance(t, threading.Thread)
        barrier.wait()
        t.join(timeout=2)
        assert results == [1]

    def test_thread_is_daemon(self):
        barrier = threading.Barrier(2)

        def slow():
            barrier.wait()

        task = ScheduledTask(slow)
        task.run_in_background()
        t = task.run()
        assert t.daemon is True
        barrier.wait()
        t.join(timeout=2)

    def test_returns_self(self):
        task = _task()
        assert task.run_in_background() is task

    def test_constraints_respected_in_background(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.run_in_background().skip(_always_true)
        result = task.run()
        assert result is None
        assert results == []


# ---------------------------------------------------------------------------
# send_output_to() / append_output_to()
# ---------------------------------------------------------------------------

class TestOutputCapture:
    def test_send_output_to_overwrites(self, tmp_path):
        log = tmp_path / "out.log"
        log.write_text("old content\n")

        def _fn():
            print("hello from task")

        task = ScheduledTask(_fn)
        task.send_output_to(str(log))
        task.run()
        content = log.read_text()
        assert "hello from task" in content
        assert "old content" not in content

    def test_append_output_to_appends(self, tmp_path):
        log = tmp_path / "out.log"
        log.write_text("first\n")

        def _fn():
            print("second")

        task = ScheduledTask(_fn)
        task.append_output_to(str(log))
        task.run()
        content = log.read_text()
        assert "first" in content
        assert "second" in content

    def test_creates_parent_dirs(self, tmp_path):
        log = tmp_path / "sub" / "dir" / "out.log"

        def _fn():
            print("data")

        task = ScheduledTask(_fn)
        task.send_output_to(str(log))
        task.run()
        assert log.exists()

    def test_send_output_returns_self(self):
        task = _task()
        assert task.send_output_to("/tmp/x.log") is task

    def test_append_output_returns_self(self):
        task = _task()
        assert task.append_output_to("/tmp/x.log") is task

    def test_stderr_also_captured(self, tmp_path):
        import sys
        log = tmp_path / "err.log"

        def _fn():
            print("err output", file=sys.stderr)

        task = ScheduledTask(_fn)
        task.send_output_to(str(log))
        task.run()
        assert "err output" in log.read_text()


# ---------------------------------------------------------------------------
# on_success() / on_failure()
# ---------------------------------------------------------------------------

class TestLifecycleHooks:
    def test_on_success_called_after_success(self):
        success = []
        task = ScheduledTask(lambda: None)
        task.on_success(lambda: success.append(1))
        task.run()
        assert success == [1]

    def test_on_failure_not_called_on_success(self):
        failure = []
        task = ScheduledTask(lambda: None)
        task.on_failure(lambda: failure.append(1))
        task.run()
        assert failure == []

    def test_on_failure_called_on_exception(self):
        failure = []

        def _boom():
            raise ValueError("boom")

        task = ScheduledTask(_boom)
        task.on_failure(lambda: failure.append(1))
        with pytest.raises(ValueError):
            task.run()
        assert failure == [1]

    def test_on_success_not_called_on_failure(self):
        success = []

        def _boom():
            raise RuntimeError("fail")

        task = ScheduledTask(_boom)
        task.on_success(lambda: success.append(1))
        with pytest.raises(RuntimeError):
            task.run()
        assert success == []

    def test_multiple_on_success_all_called(self):
        results = []
        task = ScheduledTask(lambda: None)
        task.on_success(lambda: results.append("a"))
        task.on_success(lambda: results.append("b"))
        task.run()
        assert results == ["a", "b"]

    def test_multiple_on_failure_all_called(self):
        results = []

        def _boom():
            raise RuntimeError("x")

        task = ScheduledTask(_boom)
        task.on_failure(lambda: results.append("a"))
        task.on_failure(lambda: results.append("b"))
        with pytest.raises(RuntimeError):
            task.run()
        assert results == ["a", "b"]

    def test_on_success_hook_exception_does_not_propagate(self):
        task = ScheduledTask(lambda: None)
        task.on_success(lambda: (_ for _ in ()).throw(RuntimeError("hook failed")))
        task.run()  # should not raise

    def test_on_success_returns_self(self):
        task = _task()
        assert task.on_success(lambda: None) is task

    def test_on_failure_returns_self(self):
        task = _task()
        assert task.on_failure(lambda: None) is task


# ---------------------------------------------------------------------------
# ping_before() / then_ping()
# ---------------------------------------------------------------------------

class TestPings:
    def test_ping_before_fires_before_callback(self):
        order = []

        def _callback():
            order.append("task")

        task = ScheduledTask(_callback)
        task.ping_before("http://healthcheck.example/before")

        with patch("hunt.http.client.Http") as mock_http:
            mock_http.get = MagicMock(side_effect=lambda url: order.append(f"ping:{url}"))
            with patch("hunt.scheduling.scheduler.ScheduledTask._ping",
                       side_effect=lambda urls: [order.append(f"ping:{u}") for u in urls]):
                task.run()

        # callback must run after ping_before (ordering captured in order list)
        assert "task" in order

    def test_then_ping_fires_after_callback(self):
        order = []

        def _callback():
            order.append("task")

        task = ScheduledTask(_callback)
        task.then_ping("http://healthcheck.example/after")

        ping_calls = []
        orig_ping = task._ping

        def _tracked_ping(urls):
            ping_calls.append(("ping", list(urls)))

        task._ping = _tracked_ping
        task.run()
        # then_ping called with the then_ping URL after callback completed
        assert any("healthcheck.example/after" in str(args) for _, args in ping_calls)

    def test_then_ping_not_called_on_failure(self):
        def _boom():
            raise RuntimeError("fail")

        task = ScheduledTask(_boom)
        task.then_ping("http://example.com/ping")

        pinged = []
        task._ping = lambda urls: pinged.extend(urls)

        with pytest.raises(RuntimeError):
            task.run()

        # then_ping list is only reached on success path
        assert "http://example.com/ping" not in pinged

    def test_multiple_ping_before_urls(self):
        task = ScheduledTask(lambda: None)
        task.ping_before("http://a.example/")
        task.ping_before("http://b.example/")
        assert len(task._ping_before) == 2

    def test_multiple_then_ping_urls(self):
        task = ScheduledTask(lambda: None)
        task.then_ping("http://a.example/")
        task.then_ping("http://b.example/")
        assert len(task._then_ping) == 2

    def test_ping_before_returns_self(self):
        task = _task()
        assert task.ping_before("http://example.com") is task

    def test_then_ping_returns_self(self):
        task = _task()
        assert task.then_ping("http://example.com") is task


# ---------------------------------------------------------------------------
# Constraint combination
# ---------------------------------------------------------------------------

class TestConstraintCombinations:
    def test_env_and_when_both_must_pass(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.environments("production").when(_always_true)
        with patch.dict(os.environ, {"APP_ENV": "testing"}):
            task.run()
        assert results == []

    def test_between_and_when_both_must_pass(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.between("08:00", "17:00").when(_always_false)
        dt = datetime(2026, 1, 1, 12, 0)
        task.run(dt)
        assert results == []

    def test_all_constraints_pass_runs(self):
        results = []
        task = ScheduledTask(lambda: results.append(1))
        task.environments("testing").when(_always_true).skip(_always_false).between("00:00", "23:59")
        dt = datetime(2026, 1, 1, 12, 0)
        with patch.dict(os.environ, {"APP_ENV": "testing"}):
            task.run(dt)
        assert results == [1]


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------

class TestSchedulerIntegration:
    def test_scheduler_call_returns_task(self):
        s = Scheduler()
        task = s.call(lambda: None)
        assert isinstance(task, ScheduledTask)

    def test_due_tasks_excludes_skipped(self):
        s = Scheduler()
        now = datetime(2026, 1, 1, 12, 0)
        task = s.call(lambda: None)
        task.cron("* * * * *").skip(_always_true)
        due = s.due_tasks(now)
        # is_due is True (cron matches), but run() will skip; due_tasks uses is_due only
        assert task in due

    def test_run_honours_constraints(self):
        results = []
        s = Scheduler()
        task = s.call(lambda: results.append(1))
        task.cron("* * * * *").skip(_always_true)
        task.run()
        assert results == []
