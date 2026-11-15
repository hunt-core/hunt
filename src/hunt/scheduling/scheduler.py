from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hunt.queue._logging import log_error
from hunt.scheduling.cron import matches, validate

try:
    import fcntl
except ImportError:  # pragma: no cover - only exercised on non-POSIX platforms
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - only exercised on Windows
    msvcrt = None


def _acquire_lock(fh: io.TextIOWrapper) -> bool:
    """Acquire a non-blocking exclusive lock for the schedule task."""
    if fcntl is not None:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    if msvcrt is not None:
        try:
            fh.seek(0)
            fh.write("\0")
            fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    # If the platform exposes neither locking primitive, run conservatively.
    return False


def _release_lock(fh: io.TextIOWrapper) -> None:
    """Release a previously acquired schedule lock."""
    if fcntl is not None:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return

    if msvcrt is not None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class ScheduledTask:
    def __init__(self, callback: Callable[[], Any], label: str = "") -> None:
        self._callback = callback
        self._expression = "* * * * *"
        self._description = label
        self._name: str = ""
        self._without_overlapping = False
        # Phase L additions
        self._environments: list[str] = []
        self._when_callbacks: list[Callable[[], bool]] = []
        self._skip_callbacks: list[Callable[[], bool]] = []
        self._between_start: dtime | None = None
        self._between_end: dtime | None = None
        self._run_in_background = False
        self._output_path: str | None = None
        self._output_append = False
        self._on_success: list[Callable[[], Any]] = []
        self._on_failure: list[Callable[[], Any]] = []
        self._ping_before: list[str] = []
        self._then_ping: list[str] = []
        self._timezone: ZoneInfo | None = None
        # Position in the Scheduler's task list; part of the default lock key.
        self._index = 0
        # True for scheduler.command() tasks, which write their own output file.
        self._is_command = False
        # Set when a run_in_background() task raised (read by schedule:run).
        self._background_error: BaseException | None = None

    # ------------------------------------------------------------------
    # Timing helpers
    # ------------------------------------------------------------------

    def cron(self, expression: str) -> ScheduledTask:
        """Set a standard 5-field cron expression (day-of-week: 0 or 7 = Sunday).

        Raises ``ValueError`` immediately if the expression is invalid.
        """
        self._expression = validate(expression)
        return self

    def timezone(self, tz: str | ZoneInfo) -> ScheduledTask:
        """Evaluate the cron expression and ``between()`` window in this IANA timezone."""
        self._timezone = tz if isinstance(tz, ZoneInfo) else ZoneInfo(str(tz))
        return self

    def every_minute(self) -> ScheduledTask:
        return self.cron("* * * * *")

    def every_five_minutes(self) -> ScheduledTask:
        return self.cron("*/5 * * * *")

    def every_ten_minutes(self) -> ScheduledTask:
        return self.cron("*/10 * * * *")

    def every_fifteen_minutes(self) -> ScheduledTask:
        return self.cron("*/15 * * * *")

    def every_thirty_minutes(self) -> ScheduledTask:
        return self.cron("*/30 * * * *")

    def hourly(self) -> ScheduledTask:
        return self.cron("0 * * * *")

    def hourly_at(self, minute: int) -> ScheduledTask:
        return self.cron(f"{minute} * * * *")

    def daily(self) -> ScheduledTask:
        return self.cron("0 0 * * *")

    def daily_at(self, time: str) -> ScheduledTask:
        hour, _, minute = time.partition(":")
        return self.cron(f"{minute or 0} {hour} * * *")

    def weekly(self) -> ScheduledTask:
        """Run every Sunday at 00:00."""
        return self.cron("0 0 * * 0")

    def weekly_on(self, day: int | str, time: str = "0:00") -> ScheduledTask:
        """Run weekly on *day* (0 or 7 = Sunday … 6 = Saturday, or a name like ``"MON"``)."""
        hour, _, minute = time.partition(":")
        return self.cron(f"{minute or 0} {hour} * * {day}")

    def monthly(self) -> ScheduledTask:
        return self.cron("0 0 1 * *")

    def monthly_on(self, day: int = 1, time: str = "0:00") -> ScheduledTask:
        hour, _, minute = time.partition(":")
        return self.cron(f"{minute or 0} {hour} {day} * *")

    def yearly(self) -> ScheduledTask:
        return self.cron("0 0 1 1 *")

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def description(self, text: str) -> ScheduledTask:
        self._description = text
        return self

    def name(self, name: str) -> ScheduledTask:
        self._name = name
        return self

    def without_overlapping(self) -> ScheduledTask:
        self._without_overlapping = True
        return self

    # ------------------------------------------------------------------
    # Phase L — environment / conditional constraints
    # ------------------------------------------------------------------

    def environments(self, *envs: str) -> ScheduledTask:
        """Restrict execution to the given APP_ENV values."""
        self._environments = list(envs)
        return self

    def when(self, callback: Callable[[], bool]) -> ScheduledTask:
        """Run only when callback returns True."""
        self._when_callbacks.append(callback)
        return self

    def skip(self, callback: Callable[[], bool]) -> ScheduledTask:
        """Skip when callback returns True."""
        self._skip_callbacks.append(callback)
        return self

    def between(self, start: str, end: str) -> ScheduledTask:
        """Run only between two HH:MM wall-clock times."""
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        self._between_start = dtime(sh, sm)
        self._between_end = dtime(eh, em)
        return self

    # ------------------------------------------------------------------
    # Phase L — background execution & output capture
    # ------------------------------------------------------------------

    def run_in_background(self) -> ScheduledTask:
        """Execute the task in a background thread (non-blocking).

        ``schedule:run`` waits for background tasks to finish before it exits.
        """
        self._run_in_background = True
        return self

    def send_output_to(self, path: str) -> ScheduledTask:
        """Redirect stdout/stderr to path (overwrite)."""
        self._output_path = path
        self._output_append = False
        return self

    def append_output_to(self, path: str) -> ScheduledTask:
        """Append stdout/stderr to path."""
        self._output_path = path
        self._output_append = True
        return self

    # ------------------------------------------------------------------
    # Phase L — lifecycle hooks
    # ------------------------------------------------------------------

    def on_success(self, callback: Callable[[], Any]) -> ScheduledTask:
        self._on_success.append(callback)
        return self

    def on_failure(self, callback: Callable[[], Any]) -> ScheduledTask:
        self._on_failure.append(callback)
        return self

    # ------------------------------------------------------------------
    # Phase L — health-check pings
    # ------------------------------------------------------------------

    def ping_before(self, url: str) -> ScheduledTask:
        self._ping_before.append(url)
        return self

    def then_ping(self, url: str) -> ScheduledTask:
        self._then_ping.append(url)
        return self

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _local_time(self, dt: datetime | None = None) -> datetime:
        """*dt* (default: now) expressed in the task's timezone, if one is set."""
        if self._timezone is None:
            return dt or datetime.now()
        if dt is None:
            return datetime.now(self._timezone)
        # A naive datetime is taken to be system local time.
        return dt.astimezone(self._timezone)

    def is_due(self, dt: datetime | None = None) -> bool:
        return matches(self._expression, self._local_time(dt))

    def lock_key(self) -> str:
        """Name of the ``without_overlapping`` lock; identical in every process.

        The task name if one was given, otherwise a digest of what identifies
        the task in the schedule file (expression, description, callback and
        position), never a per-process value such as ``id()``.
        """
        if self._name:
            return re.sub(r"[^A-Za-z0-9_.-]", "_", self._name)
        callback = self._callback
        identity = "\0".join(
            (
                self._expression,
                self._description,
                getattr(callback, "__module__", "") or "",
                getattr(callback, "__qualname__", "") or type(callback).__qualname__,
                str(self._index),
            )
        )
        return "task-" + hashlib.sha256(identity.encode()).hexdigest()[:32]

    def _constraints_pass(self, dt: datetime | None = None) -> bool:
        """Return False if any environment / conditional / time constraint fails."""
        # Environment check
        if self._environments:
            app_env = os.environ.get("APP_ENV", "production")
            if app_env not in self._environments:
                return False

        # Time window check
        if self._between_start is not None and self._between_end is not None:
            now = self._local_time(dt).time().replace(second=0, microsecond=0)
            if self._between_start <= self._between_end:
                # normal window: 08:00-17:00
                if not (self._between_start <= now <= self._between_end):
                    return False
            else:
                # overnight window: 22:00-06:00
                if not (now >= self._between_start or now <= self._between_end):
                    return False

        # when() callbacks — all must return True
        for fn in self._when_callbacks:
            if not fn():
                return False

        # skip() callbacks — any True means skip
        for fn in self._skip_callbacks:
            if fn():
                return False

        return True

    def _ping(self, urls: list[str]) -> None:
        if not urls:
            return
        try:
            from hunt.http.client import Http

            for url in urls:
                try:
                    Http.get(url)
                except Exception:
                    pass
        except ImportError:
            pass

    def _write_output(self, output: str) -> None:
        if not self._output_path:
            return
        mode = "a" if self._output_append else "w"
        Path(self._output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._output_path, mode) as fh:
            fh.write(output)

    def _execute(self) -> Any:
        """Run callback with output capture and lifecycle hooks."""
        self._ping(self._ping_before)
        result = None
        exc = None
        if self._output_path is not None and not self._is_command:
            buf = io.StringIO()
            old_stdout, old_stderr = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = buf
            try:
                result = self._callback()
            except Exception as e:
                exc = e
            finally:
                sys.stdout, sys.stderr = old_stdout, old_stderr
                self._write_output(buf.getvalue())
        else:
            try:
                result = self._callback()
            except Exception as e:
                exc = e

        if exc is None:
            for fn in self._on_success:
                try:
                    fn()
                except Exception:
                    pass
            self._ping(self._then_ping)
        else:
            for fn in self._on_failure:
                try:
                    fn()
                except Exception:
                    pass
            raise exc

        return result

    def run(self, dt: datetime | None = None) -> Any:
        if not self._constraints_pass(dt):
            return None

        def _do_run() -> Any:
            if self._without_overlapping:
                lock_dir = Path.cwd() / "storage" / "framework" / "schedule"
                lock_dir.mkdir(parents=True, exist_ok=True)
                lock_file = lock_dir / f"{self.lock_key()}.lock"
                fh = lock_file.open("w")
                acquired = False
                try:
                    acquired = _acquire_lock(fh)
                    if not acquired:
                        return None
                    return self._execute()
                finally:
                    if acquired:
                        _release_lock(fh)
                    fh.close()
            return self._execute()

        if self._run_in_background:
            self._background_error = None

            def _background() -> None:
                try:
                    _do_run()
                except BaseException as exc:
                    self._background_error = exc
                    log_error(f"Scheduled background task {self._description or self._name!r} failed: {exc!r}")

            # Not a daemon thread: the interpreter (and schedule:run, which
            # joins it) waits for the task instead of killing it at exit.
            t = threading.Thread(target=_background, name="hunt-schedule-task")
            t.start()
            return t

        return _do_run()

    # Human-readable summary for schedule:list
    def summary(self) -> dict[str, str]:
        return {
            "expression": self._expression,
            "description": self._description or repr(self._callback),
            "name": self._name,
            "timezone": str(self._timezone) if self._timezone is not None else "",
        }


class Scheduler:
    def __init__(self) -> None:
        self._tasks: list[ScheduledTask] = []

    def _add(self, task: ScheduledTask) -> ScheduledTask:
        task._index = len(self._tasks)
        self._tasks.append(task)
        return task

    def call(self, callback: Callable[[], Any], description: str = "") -> ScheduledTask:
        return self._add(ScheduledTask(callback, label=description))

    def command(self, *args: str, description: str = "") -> ScheduledTask:
        """Schedule a ``hunt`` console command, run as ``python -m hunt <args>``.

        The command runs with the current interpreter in a subprocess. A
        non-zero exit status counts as a failure (``on_failure`` fires and
        ``schedule:run`` reports it). With ``send_output_to``/``append_output_to``
        the subprocess's stdout and stderr go to that file.
        """
        # args must be plain strings (no shell metacharacters); they are passed
        # as a list so no shell injection is possible, but callers should
        # never interpolate untrusted input into these arguments.
        safe_args = [str(a) for a in args]
        argv = [sys.executable, "-m", "hunt", *safe_args]

        def _run() -> int:
            if task._output_path:
                Path(task._output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(task._output_path, "a" if task._output_append else "w") as fh:
                    completed = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT, check=False)
            else:
                completed = subprocess.run(argv, check=False)
            if completed.returncode != 0:
                raise RuntimeError(f"Command `hunt {' '.join(safe_args)}` exited with status {completed.returncode}.")
            return completed.returncode

        task = ScheduledTask(_run, label=description or " ".join(safe_args))
        task._is_command = True
        return self._add(task)

    def due_tasks(self, dt: datetime | None = None) -> list[ScheduledTask]:
        due = []
        for task in self._tasks:
            try:
                if task.is_due(dt):
                    due.append(task)
            except Exception as exc:
                # One unusable task must not stop the others from running.
                log_error(f"Scheduled task {task._description or task._name!r} skipped: {exc}")
        return due

    @property
    def tasks(self) -> list[ScheduledTask]:
        return list(self._tasks)
