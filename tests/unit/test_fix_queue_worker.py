"""Regression tests for the queue worker and its commands (QUE-01/03/04/07).

Every test drives the real worker functions (``process_job`` / ``run_worker``
or the ``queue:work`` command); none re-implements the loop.
"""

from __future__ import annotations

import importlib
import json
import os
import signal
import time

import pytest
from click.testing import CliRunner

from hunt.queue.job import Job
from hunt.queue.serialization import make_payload, open_payload, serialize_job

APP_KEY = "k" * 40
HAS_SIGALRM = hasattr(signal, "SIGALRM")


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    monkeypatch.setenv("APP_KEY", APP_KEY)
    monkeypatch.delenv("QUEUE_MAX_JOB_AGE", raising=False)
    monkeypatch.delenv("QUEUE_RETRY_AFTER", raising=False)
    # Retry and idle pauses are irrelevant to behaviour; skip the waiting.
    monkeypatch.setattr("hunt.queue.worker._pause", lambda seconds: None)
    Events.log = []


@pytest.fixture
def queue_db(tmp_path, monkeypatch):
    """Point the default connection at a fresh SQLite file with the queue tables."""
    conn = importlib.import_module("hunt.database.connection")

    saved_connections = dict(conn._connections)
    saved_config = dict(conn._config)
    conn._connections.clear()
    conn.configure({})
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECTION", "sqlite")
    monkeypatch.setenv("DB_DATABASE", str(tmp_path / "queue.db"))
    conn.raw(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, queue VARCHAR(255) DEFAULT 'default',"
        " payload TEXT, attempts SMALLINT DEFAULT 0, reserved_at INTEGER, available_at INTEGER, created_at INTEGER)"
    )
    conn.raw(
        "CREATE TABLE jobs_failed (id INTEGER PRIMARY KEY AUTOINCREMENT, uuid VARCHAR(36) UNIQUE,"
        " connection VARCHAR(255), queue VARCHAR(255), payload TEXT, exception TEXT, failed_at INTEGER)"
    )
    conn.raw(
        "CREATE TABLE jobs_history (id INTEGER PRIMARY KEY AUTOINCREMENT, job_class VARCHAR(255),"
        " queue VARCHAR(255), duration_ms INTEGER, finished_at INTEGER, status VARCHAR(20))"
    )
    try:
        yield conn
    finally:
        for engine in list(conn._connections.values()):
            engine.dispose()
        conn._connections.clear()
        conn._connections.update(saved_connections)
        conn.configure(saved_config)


class Events:
    log: list = []  # noqa: RUF012


class OkJob(Job):
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def handle(self) -> None:
        Events.log.append(("ok", self.value))


class BoomJob(Job):
    """Fails every time; does not set ``tries`` itself."""

    def handle(self) -> None:
        Events.log.append("boom")
        raise RuntimeError("boom")

    def failed(self, exc: Exception) -> None:
        Events.log.append(("failed-hook", type(exc).__name__, str(exc)))


class TwoTriesJob(BoomJob):
    tries = 2


class EmptyBackoffJob(BoomJob):
    backoff: list = []  # noqa: RUF012


class SteppedBackoffJob(BoomJob):
    backoff: list = [5, 15]  # noqa: RUF012


class AsyncOkJob(Job):
    async def handle(self) -> None:
        import asyncio

        await asyncio.sleep(0)
        Events.log.append("async-ok")


class AsyncBoomJob(Job):
    tries = 1

    async def handle(self) -> None:
        raise ValueError("async boom")


class SwallowTimeoutJob(Job):
    timeout = 1
    tries = 1

    def handle(self) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                time.sleep(0.05)
            except Exception:
                pass  # must not be able to swallow the timeout
        Events.log.append("finished-despite-timeout")

    def failed(self, exc: Exception) -> None:
        Events.log.append(("failed-hook", type(exc).__name__))


class SigintJob(Job):
    def handle(self) -> None:
        os.kill(os.getpid(), signal.SIGINT)
        # The signal is delivered here; the job must still be allowed to finish.
        time.sleep(0.05)
        Events.log.append("finished-after-sigint")


class MemoryDriver:
    """In-memory driver with the worker-facing API, recording every call."""

    def __init__(self) -> None:
        self.ready: list[dict] = []
        self.calls: list[tuple] = []
        self.failed: list[tuple] = []
        self.raise_on: dict[str, list[Exception]] = {}
        self._next_id = 1

    def _maybe_raise(self, name: str) -> None:
        errors = self.raise_on.get(name)
        if errors:
            error = errors.pop(0)
            if error is not None:
                raise error

    def add_raw(self, payload: str, queue: str = "default", attempts: int = 0) -> int:
        job_id = self._next_id
        self._next_id += 1
        self.ready.append({"id": job_id, "queue": queue, "attempts": attempts, "payload": payload})
        return job_id

    def push(self, job: Job) -> int:
        return self.add_raw(make_payload(serialize_job(job)), job.queue)

    def push_payload(self, body_dict: dict, queue: str = "default") -> None:
        self.calls.append(("push_payload", body_dict["class"]))
        self._maybe_raise("push_payload")
        self.add_raw(make_payload(body_dict), queue)

    def pop(self, queue: str = "default"):
        self.calls.append(("pop",))
        self._maybe_raise("pop")
        if not self.ready:
            return None
        job = dict(self.ready.pop(0))
        job["attempts"] += 1
        self._reserved = job
        return job

    def delete(self, job_id) -> None:
        self.calls.append(("delete", job_id))
        self._maybe_raise("delete")

    def release(self, job_id, delay: int = 0) -> None:
        self.calls.append(("release", job_id, delay))
        self._maybe_raise("release")
        job = dict(self._reserved)
        self.ready.append(job)

    def fail(self, job_id, queue, payload, exception) -> None:
        self.calls.append(("fail", job_id))
        self._maybe_raise("fail")
        self.failed.append((job_id, queue, payload, exception))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


def _quiet(message: str = "", err: bool = False) -> None:
    pass


def _run_one(driver, **kwargs):
    from hunt.queue.worker import process_job

    return process_job(driver, driver.pop(), output=_quiet, **kwargs)


# ---------------------------------------------------------------------------
# QUE-03: success and failure paths are separate
# ---------------------------------------------------------------------------


class TestSuccessPath:
    def test_processed_job_is_deleted(self):
        driver = MemoryDriver()
        job_id = driver.push(OkJob(4))
        assert _run_one(driver) == "processed"
        assert Events.log == [("ok", 4)]
        assert ("delete", job_id) in driver.calls
        assert "release" not in driver.names() and "fail" not in driver.names()

    def test_delete_error_after_success_does_not_release_or_rerun(self):
        driver = MemoryDriver()
        driver.push(OkJob(1))
        driver.raise_on["delete"] = [ConnectionError("store down")] * 3
        assert _run_one(driver) == "processed"
        assert Events.log == [("ok", 1)]
        assert driver.names().count("delete") == 3  # retried
        assert "release" not in driver.names()
        assert "fail" not in driver.names()

    def test_transient_delete_error_is_retried(self):
        driver = MemoryDriver()
        driver.push(OkJob(1))
        driver.raise_on["delete"] = [ConnectionError("blip")]
        assert _run_one(driver) == "processed"
        assert driver.names().count("delete") == 2

    def test_chain_is_pushed_before_delete(self):
        driver = MemoryDriver()
        first = OkJob(1)
        first.chain([OkJob(2), OkJob(3)])
        driver.push(first)
        assert _run_one(driver) == "processed"
        names = driver.names()
        assert names.index("push_payload") < names.index("delete")
        next_body = open_payload(driver.ready[0]["payload"])
        assert next_body["data"] == {"value": 2}
        assert [link["data"] for link in next_body["chain"]] == [{"value": 3}]

    def test_chain_push_error_does_not_rerun_the_finished_job(self):
        driver = MemoryDriver()
        first = OkJob(1)
        first.chain([OkJob(2)])
        driver.push(first)
        driver.raise_on["push_payload"] = [ConnectionError("down")] * 3
        assert _run_one(driver) == "processed"
        assert Events.log == [("ok", 1)]
        assert "release" not in driver.names()
        assert "delete" in driver.names()

    def test_redelivery_of_a_completed_job_is_skipped(self):
        """If delete failed, the same worker must not run the job again when it comes back."""
        from hunt.queue.worker import _RecentIds, process_job

        driver = MemoryDriver()
        payload = make_payload(serialize_job(OkJob(1)))
        driver.add_raw(payload)
        driver.add_raw(payload)  # the redelivery (or a replayed copy) of the very same payload
        completed = _RecentIds()
        assert process_job(driver, driver.pop(), output=_quiet, completed=completed) == "processed"
        assert process_job(driver, driver.pop(), output=_quiet, completed=completed) == "duplicate"
        assert Events.log == [("ok", 1)]
        assert driver.names().count("delete") == 2

    def test_async_handle_is_awaited(self):
        driver = MemoryDriver()
        driver.push(AsyncOkJob())
        assert _run_one(driver) == "processed"
        assert Events.log == ["async-ok"]

    def test_async_handle_exception_fails_the_job(self):
        driver = MemoryDriver()
        driver.push(AsyncBoomJob())
        assert _run_one(driver) == "failed"
        assert driver.failed[0][3] == "async boom"


class TestFailurePath:
    def test_failing_job_is_released_with_default_delay(self):
        driver = MemoryDriver()
        job_id = driver.push(BoomJob())
        assert _run_one(driver) == "released"
        assert ("release", job_id, 30) in driver.calls
        assert "delete" not in driver.names()

    def test_empty_backoff_list_does_not_crash(self):
        from hunt.queue.worker import backoff_delay

        assert backoff_delay([], 1) == 0
        assert backoff_delay([], 5) == 0
        driver = MemoryDriver()
        job_id = driver.push(EmptyBackoffJob())
        assert _run_one(driver) == "released"
        assert ("release", job_id, 30) in driver.calls

    def test_stepped_backoff(self):
        driver = MemoryDriver()
        job_id = driver.push(SteppedBackoffJob())
        assert _run_one(driver) == "released"
        assert ("release", job_id, 5) in driver.calls
        assert _run_one(driver) == "released"
        assert ("release", job_id, 15) in driver.calls

    def test_cli_tries_applies_when_the_job_does_not_set_tries(self):
        driver = MemoryDriver()
        driver.push(BoomJob())
        assert _run_one(driver, tries=1) == "failed"
        assert driver.names().count("fail") == 1
        assert ("failed-hook", "RuntimeError", "boom") in Events.log

        driver = MemoryDriver()
        driver.push(BoomJob())
        outcomes = [_run_one(driver, tries=5) for _ in range(5)]
        assert outcomes == ["released"] * 4 + ["failed"]

    def test_job_class_tries_wins_over_cli(self):
        driver = MemoryDriver()
        driver.push(TwoTriesJob())
        outcomes = [_run_one(driver, tries=9) for _ in range(2)]
        assert outcomes == ["released", "failed"]

    def test_release_error_does_not_propagate(self):
        driver = MemoryDriver()
        driver.push(BoomJob())
        driver.raise_on["release"] = [ConnectionError("down")] * 3
        assert _run_one(driver) == "released"

    def test_fail_error_does_not_propagate(self):
        driver = MemoryDriver()
        driver.push(BoomJob())
        driver.raise_on["fail"] = [ConnectionError("down")] * 3
        assert _run_one(driver, tries=1) == "failed"
        assert driver.failed == []

    def test_job_delivered_more_often_than_tries_is_failed_without_running(self):
        """QUE-01: a job that keeps killing its worker must not be reclaimed forever."""
        driver = MemoryDriver()
        driver.add_raw(make_payload(serialize_job(TwoTriesJob())), attempts=2)  # 3rd delivery
        assert _run_one(driver) == "failed"
        assert "boom" not in Events.log  # handle() did not run
        assert "attempted 2 time(s) without finishing" in driver.failed[0][3]

    @pytest.mark.skipif(not HAS_SIGALRM, reason="needs SIGALRM")
    def test_timeout_is_not_swallowed_by_broad_except(self):
        from hunt.queue.worker import JobTimeout

        assert issubclass(JobTimeout, BaseException) and not issubclass(JobTimeout, Exception)
        driver = MemoryDriver()
        driver.push(SwallowTimeoutJob())
        started = time.monotonic()
        assert _run_one(driver) == "failed"
        assert time.monotonic() - started < 4
        assert "finished-despite-timeout" not in Events.log
        assert ("failed-hook", "TimeoutError") in Events.log
        assert "timed out after 1s" in driver.failed[0][3]


class TestUntrustedPayloads:
    def test_unparseable_payload_fails_immediately(self):
        driver = MemoryDriver()
        job_id = driver.add_raw("{not json")
        assert _run_one(driver) == "failed"
        assert driver.names() == ["pop", "fail"]
        assert driver.failed[0][0] == job_id
        assert "not valid JSON" in driver.failed[0][3]

    def test_tampered_payload_fails_immediately(self):
        driver = MemoryDriver()
        envelope = json.loads(make_payload(serialize_job(OkJob(1))))
        envelope["body"] = envelope["body"].replace('"value": 1', '"value": 2')
        driver.add_raw(json.dumps(envelope))
        assert _run_one(driver) == "failed"
        assert Events.log == []
        assert "release" not in driver.names()
        assert "signature mismatch" in driver.failed[0][3]

    def test_job_enqueued_before_the_signing_upgrade_fails_with_a_clear_reason(self):
        from hunt.security.signing import sign

        body = json.dumps(serialize_job(OkJob(1)), sort_keys=True)
        legacy = json.dumps({"body": body, "signature": sign(body)})
        driver = MemoryDriver()
        driver.add_raw(legacy)
        assert _run_one(driver) == "failed"
        assert Events.log == []
        assert "release" not in driver.names()
        reason = driver.failed[0][3]
        assert "legacy queue signature" in reason and "queue:retry" in reason

    def test_non_job_class_is_refused(self):
        driver = MemoryDriver()
        driver.add_raw(make_payload({"class": "os.path.join", "data": {}, "tries": 1}))
        assert _run_one(driver, tries=1) == "failed"
        assert "Refusing to execute non-Job class" in driver.failed[0][3]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class TestRunWorker:
    def test_pop_error_does_not_end_the_loop(self):
        from hunt.queue.worker import run_worker

        driver = MemoryDriver()
        driver.push(OkJob(1))
        driver.raise_on["pop"] = [ConnectionError("blip")]
        reconnects = []
        driver.reconnect = lambda: reconnects.append(1)
        handled = run_worker(driver, sleep=0, max_jobs=1, output=_quiet)
        assert handled == 1
        assert Events.log == [("ok", 1)]
        assert reconnects == [1]

    def test_once_gives_up_after_repeated_pop_errors(self):
        from hunt.queue.worker import WorkerError, run_worker

        driver = MemoryDriver()
        driver.raise_on["pop"] = [ConnectionError("down")] * 10
        with pytest.raises(WorkerError):
            run_worker(driver, sleep=0, once=True, output=_quiet)
        assert driver.names().count("pop") == 3

    def test_max_jobs(self):
        from hunt.queue.worker import run_worker

        driver = MemoryDriver()
        for value in range(5):
            driver.push(OkJob(value))
        assert run_worker(driver, sleep=0, max_jobs=3, output=_quiet) == 3
        assert len(driver.ready) == 2

    def test_max_time(self, monkeypatch):
        from hunt.queue import worker

        clock = {"now": 100.0}
        monkeypatch.setattr(worker.time, "monotonic", lambda: clock["now"])
        monkeypatch.setattr(worker, "_pause", lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
        driver = MemoryDriver()
        assert worker.run_worker(driver, sleep=1, max_time=10, output=_quiet) == 0
        assert 110.0 <= clock["now"] < 112.0

    def test_should_stop_ends_an_idle_worker(self):
        from hunt.queue.worker import run_worker

        driver = MemoryDriver()
        polls = {"n": 0}

        def should_stop() -> bool:
            polls["n"] += 1
            return polls["n"] > 3

        assert run_worker(driver, sleep=5, should_stop=should_stop, output=_quiet) == 0


# ---------------------------------------------------------------------------
# Real SQLite end to end (QUE-01, QUE-07)
# ---------------------------------------------------------------------------


class TestWorkerWithDatabaseDriver:
    def test_chain_runs_through_the_database(self, queue_db):
        from hunt.queue.drivers.database import DatabaseDriver
        from hunt.queue.worker import run_worker

        driver = DatabaseDriver()
        first = OkJob(1)
        first.chain([OkJob(2)])
        driver.push(first)
        assert run_worker(driver, sleep=0, max_jobs=2, output=_quiet) == 2
        assert Events.log == [("ok", 1), ("ok", 2)]
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 0
        history = queue_db.raw("SELECT status FROM jobs_history ORDER BY id").fetchall()
        assert [row.status for row in history] == ["completed", "completed"]

    def test_crashed_job_is_retried_then_failed(self, queue_db, monkeypatch):
        """Pop, 'crash' (no settle), advance the clock: the job comes back with attempts incremented."""
        from hunt.queue.drivers.database import DatabaseDriver
        from hunt.queue.worker import process_job

        clock = {"now": 1_000_000}
        monkeypatch.setattr("hunt.queue.drivers.database._now", lambda: clock["now"])
        driver = DatabaseDriver(retry_after=60)
        driver.push(TwoTriesJob())

        assert driver.pop()["attempts"] == 1  # worker killed mid-job
        clock["now"] += 61
        assert driver.pop()["attempts"] == 2  # killed again
        clock["now"] += 61
        third = driver.pop()
        assert third["attempts"] == 3
        assert process_job(driver, third, output=_quiet) == "failed"
        assert "boom" not in Events.log
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 0
        failed = queue_db.raw("SELECT exception FROM jobs_failed").fetchone()
        assert "without finishing" in failed.exception

    def test_legacy_job_fails_then_queue_retry_re_signs_it(self, queue_db, monkeypatch):
        from hunt.console.commands import queue_failed
        from hunt.queue.drivers.database import DatabaseDriver
        from hunt.queue.manager import Queue
        from hunt.queue.worker import run_worker
        from hunt.security.signing import sign

        body = json.dumps(serialize_job(OkJob(8)), sort_keys=True)
        legacy = json.dumps({"body": body, "signature": sign(body)})
        queue_db.raw(
            "INSERT INTO jobs (queue, payload, attempts, created_at) VALUES ('default', :p, 0, 1)", {"p": legacy}
        )
        driver = DatabaseDriver()
        assert run_worker(driver, sleep=0, once=True, output=_quiet) == 1
        assert Events.log == []
        failed = queue_db.raw("SELECT id, exception FROM jobs_failed").fetchone()
        assert "legacy queue signature" in failed.exception

        monkeypatch.setattr(queue_failed, "_load_env", lambda: None)
        monkeypatch.setattr(queue_failed, "raw", queue_db.raw)
        monkeypatch.setattr(Queue, "_driver", None)
        result = CliRunner().invoke(queue_failed.queue_retry_command, [str(failed.id)])
        assert result.exit_code == 0, result.output
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs_failed").fetchone().c == 0
        requeued = queue_db.raw("SELECT payload, attempts FROM jobs").fetchone()
        assert requeued.attempts == 0
        assert open_payload(requeued.payload)["data"] == {"value": 8}  # now signed for queue.v1

        assert run_worker(driver, sleep=0, once=True, output=_quiet) == 1
        assert Events.log == [("ok", 8)]

    def test_queue_retry_all(self, queue_db, monkeypatch):
        from hunt.console.commands import queue_failed
        from hunt.queue.manager import Queue

        monkeypatch.setattr(queue_failed, "_load_env", lambda: None)
        monkeypatch.setattr(queue_failed, "raw", queue_db.raw)
        monkeypatch.setattr(Queue, "_driver", None)
        for value in (1, 2):
            queue_db.raw(
                "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
                " VALUES (:u, 'database', 'default', :p, 'x', 1)",
                {"u": f"u{value}", "p": make_payload(serialize_job(OkJob(value)))},
            )
        queue_db.raw(
            "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
            " VALUES ('forged', 'database', 'default', '{\"body\": \"{}\", \"signature\": \"nope\"}', 'x', 1)"
        )
        result = CliRunner().invoke(queue_failed.queue_retry_command, ["--all"])
        assert result.exit_code == 1  # the forged one is reported
        assert "cannot be retried" in result.output
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 2
        assert queue_db.raw("SELECT uuid FROM jobs_failed").fetchall()[0].uuid == "forged"

    def test_queue_retry_routes_redis_jobs_to_the_redis_driver(self, queue_db, monkeypatch):
        """QUE-07: a failed Redis job must go back to Redis, not into the database jobs table."""
        from hunt.console.commands import queue_failed

        pushed = []

        class StubRedisDriver:
            def push_payload(self, body_dict, queue="default"):
                pushed.append((body_dict, queue))

        monkeypatch.setattr(queue_failed, "_load_env", lambda: None)
        monkeypatch.setattr(queue_failed, "raw", queue_db.raw)
        monkeypatch.setattr(queue_failed, "_driver_for", lambda name: StubRedisDriver() if name == "redis" else None)
        queue_db.raw(
            "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
            " VALUES ('u1', 'redis', 'emails', :p, 'x', 1)",
            {"p": (failed_payload := make_payload(serialize_job(OkJob(5))))},
        )
        result = CliRunner().invoke(queue_failed.queue_retry_command, ["1"])
        assert result.exit_code == 0, result.output
        assert len(pushed) == 1
        body, queue = pushed[0]
        assert queue == "emails" and body["data"] == {"value": 5}
        # A retry gets a new identity: an id derived from the failed job's id
        # (so the same failed payload retried twice still runs once) and a new timestamp.
        assert body["id"] != open_payload(failed_payload)["id"] and "created_at" not in body
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 0
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs_failed").fetchone().c == 0


# ---------------------------------------------------------------------------
# queue:work command
# ---------------------------------------------------------------------------


class TestQueueWorkCommand:
    def _invoke(self, driver, args, tmp_path, monkeypatch):
        from hunt.console.commands.queue_work import queue_work_command
        from hunt.queue.manager import Queue

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Queue, "_driver", driver)
        return CliRunner().invoke(queue_work_command, args)

    def test_max_jobs_option(self, tmp_path, monkeypatch):
        driver = MemoryDriver()
        for value in range(3):
            driver.push(OkJob(value))
        result = self._invoke(driver, ["--max-jobs", "2", "--sleep", "0"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert Events.log == [("ok", 0), ("ok", 1)]

    def test_tries_option_reaches_the_worker(self, tmp_path, monkeypatch):
        driver = MemoryDriver()
        driver.push(BoomJob())
        result = self._invoke(driver, ["--once", "--tries", "1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert len(driver.failed) == 1

    def test_once_exits_non_zero_when_the_store_is_unreachable(self, tmp_path, monkeypatch):
        driver = MemoryDriver()
        driver.raise_on["pop"] = [ConnectionError("down")] * 10
        result = self._invoke(driver, ["--once"], tmp_path, monkeypatch)
        assert result.exit_code == 1

    @pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
    def test_sigint_finishes_the_current_job_then_stops(self, tmp_path, monkeypatch):
        """QUE-01/03: Ctrl+C drains like SIGTERM instead of abandoning the job in flight."""
        before = signal.getsignal(signal.SIGINT)
        driver = MemoryDriver()
        job_id = driver.push(SigintJob())
        driver.push(OkJob(99))  # must not be started after the stop request
        result = self._invoke(driver, ["--sleep", "0"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert Events.log == ["finished-after-sigint"]
        assert ("delete", job_id) in driver.calls
        assert len(driver.ready) == 1
        assert "finishing current job" in result.output
        assert signal.getsignal(signal.SIGINT) is before  # handlers restored

    def test_python_dash_m_hunt_lists_the_command(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "hunt", "queue:work", "--help"], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0
        assert "--max-jobs" in result.stdout and "--max-time" in result.stdout
