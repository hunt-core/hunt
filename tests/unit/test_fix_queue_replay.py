"""Cross-worker replay protection for queue jobs (the open part of QUE-04).

A signed payload must run at most once per legitimate enqueue, on any worker
and across restarts, while redelivery (retry, crash recovery, ``queue:retry``,
chains, delayed jobs) keeps working.

The database driver runs against a real SQLite file whose tables are created
by the migration that ``hunt queue:table`` generates. The Redis driver runs
against ``ReplayFakeRedis``: the fake from ``test_fix_queue_drivers`` plus
``SET`` (``NX``/``PX``), ``GET`` and ``DEL``, the only extra commands used.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os

import pytest
from click.testing import CliRunner

from hunt.queue.job import Job
from hunt.queue.serialization import DEFAULT_PROCESSED_RETENTION, open_payload
from tests.unit.test_fix_queue_drivers import Clock, FakeRedis

APP_KEY = "r" * 40
DAY = 24 * 60 * 60


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    from hunt.log.manager import Log

    monkeypatch.delenv("LOG_CHANNEL", raising=False)
    monkeypatch.setattr(Log, "_default_logger", None)
    monkeypatch.setenv("APP_KEY", APP_KEY)
    for name in ("QUEUE_MAX_JOB_AGE", "QUEUE_RETRY_AFTER", "QUEUE_PROCESSED_RETENTION", "QUEUE_DRIVER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("hunt.queue.worker._pause", lambda seconds: None)


@pytest.fixture
def clock(monkeypatch):
    """One clock for the drivers and for payload timestamps."""
    clock = Clock()
    monkeypatch.setattr("hunt.queue.drivers.database._now", lambda: int(clock.now))
    monkeypatch.setattr("hunt.queue.drivers.redis._now", lambda: clock.now)
    monkeypatch.setattr("hunt.queue.serialization._now", lambda: clock.now)
    return clock


def _load_migration(path):
    spec = importlib.util.spec_from_file_location(f"replay_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generate_migration(tmp_path, args):
    """Run ``hunt queue:table`` in *tmp_path* and return the migration module it wrote."""
    from hunt.console.commands.queue_table import queue_table_command

    before = set((tmp_path / "database" / "migrations").glob("*.py")) if (tmp_path / "database").exists() else set()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = CliRunner().invoke(queue_table_command, args, catch_exceptions=False)
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    (created,) = set((tmp_path / "database" / "migrations").glob("*.py")) - before
    return created, _load_migration(created)


@pytest.fixture
def bare_db(tmp_path, monkeypatch):
    """The default connection on a fresh, empty SQLite file."""
    conn = importlib.import_module("hunt.database.connection")

    saved_connections = dict(conn._connections)
    saved_config = dict(conn._config)
    conn._connections.clear()
    conn.configure({})
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECTION", "sqlite")
    monkeypatch.setenv("DB_DATABASE", str(tmp_path / "queue.db"))
    try:
        yield conn
    finally:
        for engine in list(conn._connections.values()):
            engine.dispose()
        conn._connections.clear()
        conn._connections.update(saved_connections)
        conn.configure(saved_config)


@pytest.fixture
def queue_db(bare_db, tmp_path):
    """All queue tables, created by the migration ``hunt queue:table`` generates."""
    _path, module = _generate_migration(tmp_path, [])
    module.CreateJobsTables().up()
    return bare_db


@pytest.fixture
def legacy_db(bare_db):
    """An app that was created before jobs_processed existed."""
    bare_db.raw(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, queue VARCHAR(255) DEFAULT 'default',"
        " payload TEXT, attempts SMALLINT DEFAULT 0, reserved_at INTEGER, available_at INTEGER, created_at INTEGER)"
    )
    bare_db.raw(
        "CREATE TABLE jobs_failed (id INTEGER PRIMARY KEY AUTOINCREMENT, uuid VARCHAR(36) UNIQUE,"
        " connection VARCHAR(255), queue VARCHAR(255), payload TEXT, exception TEXT, failed_at INTEGER)"
    )
    return bare_db


class Runs:
    log: list = []  # noqa: RUF012
    during: object = None  # called from inside HookJob.handle()
    flaky_failures = 0


@pytest.fixture(autouse=True)
def _reset_runs():
    Runs.log = []
    Runs.during = None
    Runs.flaky_failures = 0


class OkJob(Job):
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def handle(self) -> None:
        Runs.log.append(self.value)


class HookJob(Job):
    """Runs ``Runs.during`` in the middle of the handler (once)."""

    def __init__(self, value: int = 0) -> None:
        self.value = value

    def handle(self) -> None:
        Runs.log.append(("start", self.value))
        hook, Runs.during = Runs.during, None
        if hook is not None:
            hook()
        Runs.log.append(("end", self.value))


class FlakyJob(Job):
    """Fails ``Runs.flaky_failures`` times, then succeeds."""

    tries = 3
    backoff = 10

    def handle(self) -> None:
        if Runs.flaky_failures > 0:
            Runs.flaky_failures -= 1
            Runs.log.append("flaky-failed")
            raise RuntimeError("not yet")
        Runs.log.append("flaky-ok")


class OneShotJob(Job):
    """Like FlakyJob, but a single failure sends it to the failed store."""

    tries = 1

    def handle(self) -> None:
        if Runs.flaky_failures > 0:
            Runs.flaky_failures -= 1
            Runs.log.append("oneshot-failed")
            raise RuntimeError("boom")
        Runs.log.append("oneshot-ok")


class Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, message: str = "", err: bool = False) -> None:
        self.lines.append(message)

    def count(self, needle: str) -> int:
        return sum(1 for line in self.lines if needle in line)


def _db_driver(**kwargs):
    from hunt.queue.drivers.database import DatabaseDriver

    return DatabaseDriver(**kwargs)


def _work(driver, out=None, **kwargs):
    """One ``queue:work --once`` by a worker that shares nothing with earlier ones."""
    from hunt.queue.worker import run_worker

    kwargs.setdefault("once", True)
    return run_worker(driver, sleep=0, output=out or Output(), **kwargs)


def _count(db, table: str) -> int:
    return db.raw(f"SELECT COUNT(*) AS c FROM {table}").fetchone().c


def _insert_job(db, payload: str, queue: str = "default") -> int:
    db.raw(
        "INSERT INTO jobs (queue, payload, attempts, available_at, created_at) VALUES (:q, :p, 0, NULL, 1)",
        {"q": queue, "p": payload},
    )
    return db.raw("SELECT MAX(id) AS id FROM jobs").fetchone().id


def _processed_rows(db) -> list[tuple]:
    rows = db.raw("SELECT job_id, processed_at, reserved_until FROM jobs_processed ORDER BY id").fetchall()
    return [(row.job_id, row.processed_at, row.reserved_until) for row in rows]


def _completed_history(db) -> int:
    return db.raw("SELECT COUNT(*) AS c FROM jobs_history WHERE status = 'completed'").fetchone().c


# ---------------------------------------------------------------------------
# Database driver
# ---------------------------------------------------------------------------


class TestDatabaseReplay:
    def test_completed_payload_reinserted_is_not_run_by_another_worker(self, queue_db, clock):
        _db_driver().push(OkJob(1))
        payload = queue_db.raw("SELECT payload FROM jobs").fetchone().payload
        job_uid = open_payload(payload)["id"]

        assert _work(_db_driver()) == 1
        assert Runs.log == [1]
        assert _count(queue_db, "jobs") == 0
        assert _processed_rows(queue_db) == [(job_uid, int(clock.now), None)]

        # Someone with write access to the store puts the signed payload back.
        _insert_job(queue_db, payload)
        clock.advance(3600)
        out = Output()
        assert _work(_db_driver(), out) == 1  # a new driver and worker: no shared memory
        assert Runs.log == [1]  # not executed again
        assert _count(queue_db, "jobs") == 0  # and removed
        assert out.count("already finished") == 1
        assert _count(queue_db, "jobs_failed") == 0
        assert _completed_history(queue_db) == 1

    def test_completion_and_processed_id_are_one_transaction(self, queue_db, clock, monkeypatch):
        """If the processed id cannot be written, the job row must still be there."""
        from hunt.queue.drivers import database

        driver = _db_driver()
        driver.push(OkJob(1))
        job = driver.pop()
        job_uid = open_payload(job["payload"])["id"]
        assert driver.claim_run(job_uid, job) == "claimed"

        from sqlalchemy import text

        def broken(conn, uid, stamp):
            conn.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job["id"]})
            raise RuntimeError("disk full")

        monkeypatch.setattr(database, "_mark_finished", broken)
        with pytest.raises(RuntimeError, match="disk full"):
            driver.complete(job["id"], job_uid)
        assert _count(queue_db, "jobs") == 1  # the delete inside the transaction was rolled back
        assert _processed_rows(queue_db)[0][1] is None  # still only a lease, not "finished"

    def test_job_id_is_unique_in_the_processed_table(self, queue_db):
        from sqlalchemy.exc import IntegrityError

        queue_db.raw("INSERT INTO jobs_processed (job_id, processed_at) VALUES ('abc', 1)")
        with pytest.raises(IntegrityError):
            queue_db.raw("INSERT INTO jobs_processed (job_id, processed_at) VALUES ('abc', 2)")

    def test_two_workers_finishing_the_same_id_record_one_completion(self, queue_db, clock):
        """A job that outlives retry_after is taken over and runs twice; only one run is recorded."""
        first, second = _db_driver(retry_after=60), _db_driver(retry_after=60)
        first.push(HookJob(7))
        second_out = Output()

        def other_worker_takes_over():
            clock.advance(61)  # the first worker's reservation has expired
            assert _work(second, second_out) == 1

        Runs.during = other_worker_takes_over
        first_out = Output()
        assert _work(first, first_out) == 1

        assert Runs.log == [("start", 7), ("start", 7), ("end", 7), ("end", 7)]
        assert second_out.count("Processed:") == 1
        assert first_out.count("Processed:") == 0
        assert first_out.count("also completed by another worker") == 1
        assert _completed_history(queue_db) == 1
        assert len(_processed_rows(queue_db)) == 1 and _processed_rows(queue_db)[0][1] is not None
        assert _count(queue_db, "jobs") == 0

    def test_losing_the_insert_race_is_handled(self, queue_db, clock, monkeypatch):
        """Simulated: our INSERT of the processed id hits the unique index because another worker's
        commit landed between our check and our insert. The transaction is run again and sees it."""
        from sqlalchemy.exc import IntegrityError

        from hunt.queue.drivers import database

        driver = _db_driver()
        driver.push(OkJob(1))
        job = driver.pop()
        job_uid = open_payload(job["payload"])["id"]
        # The other worker's committed record.
        queue_db.raw("INSERT INTO jobs_processed (job_id, processed_at) VALUES (:uid, 5)", {"uid": job_uid})

        real = database._mark_finished
        calls = []

        def racing(conn, uid, stamp):
            calls.append(uid)
            if len(calls) == 1:
                raise IntegrityError("INSERT INTO jobs_processed", {}, Exception("UNIQUE constraint failed"))
            return real(conn, uid, stamp)

        monkeypatch.setattr(database, "_mark_finished", racing)
        assert driver.complete(job["id"], job_uid) is False  # the other worker's record stands
        assert len(calls) == 2
        assert _count(queue_db, "jobs") == 0
        assert _processed_rows(queue_db) == [(job_uid, 5, None)]

    def test_duplicate_of_a_running_job_is_postponed_then_dropped(self, queue_db, clock):
        first, second = _db_driver(), _db_driver()
        first.push(HookJob(3))
        payload = queue_db.raw("SELECT payload FROM jobs").fetchone().payload
        second_out = Output()

        def duplicate_arrives_while_running():
            copy_id = _insert_job(queue_db, payload)
            assert _work(second, second_out) == 1
            row = queue_db.raw("SELECT attempts, reserved_at, available_at FROM jobs WHERE id = :id", {"id": copy_id})
            row = row.fetchone()
            # Put back, not counted as an attempt, and not run.
            assert (row.attempts, row.reserved_at) == (0, None) and row.available_at > clock.now

        Runs.during = duplicate_arrives_while_running
        assert _work(first) == 1
        assert Runs.log == [("start", 3), ("end", 3)]
        assert second_out.count("being run by another worker") == 1

        clock.advance(10)
        assert _work(second, second_out) == 1
        assert Runs.log == [("start", 3), ("end", 3)]
        assert second_out.count("already finished") == 1
        assert _count(queue_db, "jobs") == 0

    def test_crash_before_completion_is_redelivered_and_runs(self, queue_db, clock):
        crashed = _db_driver(retry_after=60)
        crashed.push(OkJob(4))
        job = crashed.pop()
        job_uid = open_payload(job["payload"])["id"]
        assert crashed.claim_run(job_uid, job) == "claimed"
        # ... the worker dies here: nothing is completed, released or cleaned up.

        survivor = _db_driver(retry_after=60)
        assert _work(survivor) == 0  # still reserved
        assert Runs.log == []

        clock.advance(60)
        out = Output()
        assert _work(survivor, out) == 1
        assert Runs.log == [4]  # replay protection did not block the redelivery
        assert out.count("Processed:") == 1
        assert _processed_rows(queue_db) == [(job_uid, int(clock.now), None)]
        assert _count(queue_db, "jobs") == 0

    def test_crash_after_the_handler_but_before_completion_runs_again(self, queue_db, clock):
        """At-least-once: without a recorded completion the job is delivered again."""
        from hunt.queue.worker import process_job

        crashed = _db_driver(retry_after=60)
        crashed.push(OkJob(5))
        job = crashed.pop()

        def dies(*args, **kwargs):
            raise KeyboardInterrupt  # not an Exception: the worker process is going down

        crashed.complete = dies
        with pytest.raises(KeyboardInterrupt):
            process_job(crashed, job, output=Output())
        assert Runs.log == [5]

        clock.advance(60)
        assert _work(_db_driver(retry_after=60)) == 1
        assert Runs.log == [5, 5]
        assert _count(queue_db, "jobs") == 0

    def test_released_job_runs_again_with_the_same_id_and_is_recorded_once(self, queue_db, clock):
        Runs.flaky_failures = 1
        _db_driver().push(FlakyJob())
        job_uid = open_payload(queue_db.raw("SELECT payload FROM jobs").fetchone().payload)["id"]

        out = Output()
        assert _work(_db_driver(), out) == 1
        assert Runs.log == ["flaky-failed"]
        assert _processed_rows(queue_db) == []  # the lease was given back with the release
        assert _count(queue_db, "jobs") == 1

        assert _work(_db_driver(), out) == 0  # backing off
        clock.advance(10)
        # Released, not re-signed: it is still the same payload with the same id.
        assert open_payload(queue_db.raw("SELECT payload FROM jobs").fetchone().payload)["id"] == job_uid
        assert _work(_db_driver(), out) == 1
        assert Runs.log == ["flaky-failed", "flaky-ok"]
        assert _processed_rows(queue_db) == [(job_uid, int(clock.now), None)]
        assert _completed_history(queue_db) == 1
        assert _count(queue_db, "jobs") == 0 and _count(queue_db, "jobs_failed") == 0

    def test_copy_of_a_failed_job_is_not_run_but_queue_retry_runs_it_once(self, queue_db, clock, monkeypatch):
        from hunt.console.commands import queue_failed
        from hunt.queue.manager import Queue

        Runs.flaky_failures = 1
        _db_driver().push(OneShotJob())
        assert _work(_db_driver()) == 1
        assert Runs.log == ["oneshot-failed"]
        failed = queue_db.raw("SELECT * FROM jobs_failed").fetchone()
        failed_uid = open_payload(failed.payload)["id"]
        assert _processed_rows(queue_db) == [(failed_uid, int(clock.now), None)]  # finished, for good

        # The payload is copied out of jobs_failed and put back on the queue.
        _insert_job(queue_db, failed.payload)
        out = Output()
        assert _work(_db_driver(), out) == 1
        assert Runs.log == ["oneshot-failed"]  # the copy did not run
        assert out.count("already finished") == 1
        assert _count(queue_db, "jobs") == 0

        # The legitimate way back: `hunt queue:retry`.
        monkeypatch.setattr(queue_failed, "_load_env", lambda: None)
        monkeypatch.setattr(queue_failed, "raw", queue_db.raw)
        monkeypatch.setattr(Queue, "_driver", None)
        # A second copy of the failed row is planted so that the same job is retried twice.
        queue_db.raw(
            "INSERT INTO jobs_failed (uuid, connection, queue, payload, exception, failed_at)"
            " VALUES ('planted', 'database', 'default', :p, 'x', 1)",
            {"p": failed.payload},
        )
        result = CliRunner().invoke(queue_failed.queue_retry_command, ["--all"])
        assert result.exit_code == 0, result.output
        assert _count(queue_db, "jobs_failed") == 0
        retried = [open_payload(row.payload) for row in queue_db.raw("SELECT payload FROM jobs").fetchall()]
        assert len(retried) == 2
        assert retried[0]["id"] == retried[1]["id"] != failed_uid  # one new identity for both retries

        assert _work(_db_driver(), max_jobs=2, once=False, should_stop=_stop_when_empty(queue_db)) == 2
        assert Runs.log == ["oneshot-failed", "oneshot-ok"]  # the retry ran exactly once
        assert _count(queue_db, "jobs") == 0 and _count(queue_db, "jobs_failed") == 0
        assert _completed_history(queue_db) == 1

    def test_forged_payload_cannot_mark_another_jobs_id_as_finished(self, queue_db, clock):
        """Only an id from a verified payload is recorded; otherwise a forgery could suppress a real job."""
        driver = _db_driver()
        driver.push(OkJob(9))
        real = queue_db.raw("SELECT id, payload FROM jobs").fetchone()
        envelope = json.loads(real.payload)
        envelope["signature"] = "0" * 64
        queue_db.raw("UPDATE jobs SET available_at = :at WHERE id = :id", {"at": int(clock.now) + 5, "id": real.id})
        _insert_job(queue_db, json.dumps(envelope))

        assert _work(_db_driver()) == 1
        assert Runs.log == [] and _count(queue_db, "jobs_failed") == 1
        assert _processed_rows(queue_db) == []

        clock.advance(5)
        assert _work(_db_driver()) == 1
        assert Runs.log == [9]

    def test_store_error_postpones_the_job_instead_of_running_it(self, queue_db, clock):
        driver = _db_driver()
        driver.push(OkJob(1))

        def unreachable(job_uid, job_data=None):
            raise ConnectionError("database is down")

        driver.claim_run = unreachable
        out = Output()
        assert _work(driver, out) == 1
        assert Runs.log == []
        assert out.count("did not answer") == 1
        row = queue_db.raw("SELECT attempts, reserved_at, available_at FROM jobs").fetchone()
        assert (row.attempts, row.reserved_at, row.available_at) == (0, None, int(clock.now) + 30)

    def test_chained_job_runs_once_even_if_the_continuation_is_pushed_twice(self, queue_db, clock):
        _db_driver().push(OkJob(1).chain([OkJob(2)]))
        assert _work(_db_driver()) == 1
        continuation = queue_db.raw("SELECT payload FROM jobs").fetchone().payload
        # As if the first job had been delivered twice and pushed its continuation twice.
        again = dict(open_payload(continuation))
        again.pop("created_at")
        _db_driver().push_payload(again)
        assert _count(queue_db, "jobs") == 2

        out = Output()
        assert _work(_db_driver(), out, once=False, max_jobs=2) == 2
        assert Runs.log == [1, 2]
        assert out.count("already finished") == 1
        assert _count(queue_db, "jobs") == 0


def _stop_when_empty(db):
    return lambda: _count(db, "jobs") == 0


# ---------------------------------------------------------------------------
# Staleness, delays and retention
# ---------------------------------------------------------------------------


class TestStalenessAndRetention:
    def test_delayed_job_with_a_delay_longer_than_retention_runs_when_due(self, queue_db, clock):
        delay = 30 * DAY
        assert delay > DEFAULT_PROCESSED_RETENTION
        _db_driver().later(delay, OkJob(1))
        body = open_payload(queue_db.raw("SELECT payload FROM jobs").fetchone().payload)
        assert body["available_at"] == body["created_at"] + delay  # signed with the payload

        clock.advance(delay - 1)
        assert _work(_db_driver()) == 0
        clock.advance(3600)
        assert _work(_db_driver()) == 1
        assert Runs.log == [1]
        assert _count(queue_db, "jobs_failed") == 0

    def test_delayed_job_goes_stale_once_it_is_overdue_by_more_than_retention(self, queue_db, clock):
        _db_driver().later(30 * DAY, OkJob(1))
        clock.advance(30 * DAY + DEFAULT_PROCESSED_RETENTION + 60)
        assert _work(_db_driver()) == 1
        assert Runs.log == []
        assert "stale payload" in queue_db.raw("SELECT exception FROM jobs_failed").fetchone().exception

    def test_old_non_delayed_payload_is_rejected_as_stale_and_recorded_as_failed(self, queue_db, clock):
        _db_driver().push(OkJob(1))
        clock.advance(DEFAULT_PROCESSED_RETENTION + 60)
        out = Output()
        assert _work(_db_driver(), out) == 1
        assert Runs.log == []
        assert _count(queue_db, "jobs") == 0
        failed = queue_db.raw("SELECT exception FROM jobs_failed").fetchone()
        assert "QUEUE_PROCESSED_RETENTION" in failed.exception and "stale payload" in failed.exception
        assert "queue:retry" in failed.exception
        assert out.count("Rejected:") == 1

    def test_job_just_inside_the_window_still_runs(self, queue_db, clock):
        _db_driver().push(OkJob(1))
        clock.advance(DEFAULT_PROCESSED_RETENTION - 60)
        assert _work(_db_driver()) == 1
        assert Runs.log == [1]

    def test_retention_from_config_and_environment(self, queue_db, clock, monkeypatch):
        from hunt.queue.manager import _build_driver
        from hunt.queue.serialization import keep_processed_for, max_payload_age, processed_retention

        assert processed_retention() == DEFAULT_PROCESSED_RETENTION
        monkeypatch.setenv("QUEUE_PROCESSED_RETENTION", "120")
        assert processed_retention() == 120
        assert processed_retention(600) == 600  # config/queue.py wins over the environment
        driver = _build_driver("database", processed_retention=600)
        assert driver.processed_retention == 600

        driver.push(OkJob(1))
        clock.advance(601)
        assert _work(driver) == 1
        assert Runs.log == [] and _count(queue_db, "jobs_failed") == 1

        # QUEUE_MAX_JOB_AGE stays an explicit override of the accepted age, and
        # ids are then kept at least that long so pruning stays safe.
        monkeypatch.setenv("QUEUE_MAX_JOB_AGE", "5000")
        assert max_payload_age(600) == 5000 and keep_processed_for(600) == 5000
        driver.push(OkJob(2))
        clock.advance(601)
        assert _work(driver) == 1
        assert Runs.log == [2]
        monkeypatch.setenv("QUEUE_MAX_JOB_AGE", "60")
        assert max_payload_age(600) == 60 and keep_processed_for(600) == 600
        # 0 keeps ids forever and never calls a payload stale.
        monkeypatch.delenv("QUEUE_MAX_JOB_AGE")
        assert max_payload_age(0) == 0 and keep_processed_for(0) == 0

    def test_prune_removes_only_ids_older_than_retention(self, queue_db, clock):
        driver = _db_driver(processed_retention=1000)
        now = int(clock.now)
        rows = [
            ("old-done", now - 1001, None),
            ("fresh-done", now - 999, None),
            ("abandoned-lease", None, now - 1001),
            ("live-lease", None, now + 30),
        ]
        for job_id, processed_at, reserved_until in rows:
            queue_db.raw(
                "INSERT INTO jobs_processed (job_id, processed_at, reserved_until) VALUES (:j, :p, :r)",
                {"j": job_id, "p": processed_at, "r": reserved_until},
            )
        assert driver.prune_processed() == 2
        assert [row[0] for row in _processed_rows(queue_db)] == ["fresh-done", "live-lease"]
        assert _db_driver(processed_retention=0).prune_processed() == 0  # 0 = keep forever

    def test_id_of_a_job_that_ran_before_it_was_due_is_kept_from_its_due_time(self, queue_db, clock):
        """Otherwise the id could be pruned while the payload is still young enough to be accepted."""
        _db_driver().later(30 * DAY, OkJob(1))
        row = queue_db.raw("SELECT id, payload FROM jobs").fetchone()
        due = open_payload(row.payload)["available_at"]
        queue_db.raw("UPDATE jobs SET available_at = NULL WHERE id = :id", {"id": row.id})  # moved forward by hand

        assert _work(_db_driver()) == 1
        assert Runs.log == [1]
        assert _processed_rows(queue_db)[0][1] == due

        clock.advance(20 * DAY)
        assert _db_driver().prune_processed() == 0
        _insert_job(queue_db, row.payload)
        assert _work(_db_driver()) == 1
        assert Runs.log == [1]  # the replay was recognised

    def test_worker_prunes_at_start_and_then_only_every_few_minutes(self, queue_db, clock, monkeypatch):
        from hunt.queue import worker

        queue_db.raw(
            "INSERT INTO jobs_processed (job_id, processed_at) VALUES ('ancient', :at)",
            {"at": int(clock.now) - DEFAULT_PROCESSED_RETENTION - 5},
        )
        driver = _db_driver()
        prunes = []
        real_prune = driver.prune_processed
        driver.prune_processed = lambda: prunes.append(real_prune())
        for value in range(4):
            driver.push(OkJob(value))

        ticks = iter(range(0, 10_000, 100))  # each look at the monotonic clock is 100s later
        monkeypatch.setattr(worker.time, "monotonic", lambda: next(ticks))
        assert _work(driver, once=False, max_jobs=4) == 4
        assert Runs.log == [0, 1, 2, 3]
        assert prunes[0] == 1  # at start
        assert 1 < len(prunes) < 5  # again later, but not once per job
        assert "ancient" not in [row[0] for row in _processed_rows(queue_db)]

    def test_prune_command(self, queue_db, clock, tmp_path, monkeypatch):
        from hunt.console.commands.queue_failed import queue_prune_processed_command
        from hunt.queue.manager import Queue

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Queue, "_driver", _db_driver(processed_retention=1000))
        now = int(clock.now)
        queue_db.raw("INSERT INTO jobs_processed (job_id, processed_at) VALUES ('old', :at)", {"at": now - 2000})
        queue_db.raw("INSERT INTO jobs_processed (job_id, processed_at) VALUES ('new', :at)", {"at": now - 10})

        result = CliRunner().invoke(queue_prune_processed_command, [])
        assert result.exit_code == 0, result.output
        assert "Pruned 1 processed job id(s) older than 1000 seconds" in result.output
        assert [row[0] for row in _processed_rows(queue_db)] == ["new"]

    def test_prune_command_says_so_when_the_table_is_missing(self, legacy_db, tmp_path, monkeypatch):
        from hunt.console.commands.queue_failed import queue_prune_processed_command
        from hunt.queue.manager import Queue

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Queue, "_driver", _db_driver())
        result = CliRunner().invoke(queue_prune_processed_command, [])
        assert result.exit_code == 1
        assert "queue:table --processed-only" in result.output


# ---------------------------------------------------------------------------
# Existing apps: no jobs_processed table yet
# ---------------------------------------------------------------------------


class TestMissingProcessedTable:
    def test_one_warning_and_the_worker_keeps_processing(self, legacy_db, clock):
        driver = _db_driver()
        driver.push(OkJob(1))
        payload = legacy_db.raw("SELECT payload FROM jobs").fetchone().payload
        driver.push(OkJob(2))
        _insert_job(legacy_db, payload)  # a replay of the first job, seen by the same worker
        driver.push(OkJob(3))

        out = Output()
        assert _work(driver, out, once=False, max_jobs=4) == 4
        assert Runs.log == [1, 2, 3]  # the in-process duplicate check is still active
        assert out.count("Skipped:") == 1
        assert out.count("replay protection is NOT active") == 1
        warning = next(line for line in out.lines if "NOT active" in line)
        assert "jobs_processed" in warning and "hunt queue:table --processed-only" in warning
        assert "hunt migrate" in warning
        assert _count(legacy_db, "jobs") == 0 and _count(legacy_db, "jobs_failed") == 0

    def test_without_the_table_another_worker_does_run_a_replay(self, legacy_db, clock):
        """The warning is honest: this is exactly the protection that is missing."""
        _db_driver().push(OkJob(1))
        payload = legacy_db.raw("SELECT payload FROM jobs").fetchone().payload
        assert _work(_db_driver()) == 1
        _insert_job(legacy_db, payload)
        assert _work(_db_driver()) == 1
        assert Runs.log == [1, 1]

    def test_failed_jobs_and_retries_still_work_without_the_table(self, legacy_db, clock):
        Runs.flaky_failures = 1
        _db_driver().push(FlakyJob())
        assert _work(_db_driver()) == 1
        clock.advance(10)
        assert _work(_db_driver()) == 1
        assert Runs.log == ["flaky-failed", "flaky-ok"]

        Runs.flaky_failures = 1
        _db_driver().push(OneShotJob())
        assert _work(_db_driver()) == 1
        assert _count(legacy_db, "jobs_failed") == 1 and _count(legacy_db, "jobs") == 0

    def test_protection_starts_once_the_migration_has_run(self, legacy_db, clock, tmp_path):
        from hunt.queue.worker import ReplayGuard

        driver = _db_driver()
        out = Output()
        guard = ReplayGuard(driver, out)
        guard.maintain(force=True)
        assert guard.active is False and out.count("NOT active") == 1

        path, module = _generate_migration(tmp_path, ["--processed-only"])
        assert path.name.endswith("_create_jobs_processed_table.py")
        module.CreateJobsProcessedTable().up()

        guard.maintain(force=True)
        assert guard.active is True
        assert out.count("NOT active") == 1 and out.count("now active") == 1

        driver.push(OkJob(1))
        payload = legacy_db.raw("SELECT payload FROM jobs").fetchone().payload
        assert _work(_db_driver()) == 1
        _insert_job(legacy_db, payload)
        assert _work(_db_driver()) == 1
        assert Runs.log == [1]

    def test_processed_only_migration_can_be_rolled_back(self, legacy_db, tmp_path):
        _path, module = _generate_migration(tmp_path, ["--processed-only"])
        migration = module.CreateJobsProcessedTable()
        migration.up()
        assert _db_driver().processed_store_ready() is True
        migration.down()
        assert _db_driver().processed_store_ready() is False
        assert _count(legacy_db, "jobs") == 0  # the other tables are untouched

    def test_driver_without_a_store_is_announced_once(self, clock):
        from tests.unit.test_fix_queue_worker import MemoryDriver

        driver = MemoryDriver()
        driver.push(OkJob(1))
        driver.push(OkJob(2))
        out = Output()
        assert _work(driver, out, once=False, max_jobs=2) == 2
        assert Runs.log == [1, 2]
        assert out.count("NOT active") == 1
        assert "MemoryDriver queue driver has no processed-id store" in "".join(out.lines)


# ---------------------------------------------------------------------------
# Redis driver (in-test fake)
# ---------------------------------------------------------------------------


class ReplayFakeRedis(FakeRedis):
    """Adds the three commands replay protection uses: SET (NX, PX), GET, DEL."""

    def __init__(self, clock) -> None:
        super().__init__()
        self.clock = clock
        self.strings: dict[str, tuple] = {}  # key -> (value, expires_at or None)

    def _live(self, key):
        entry = self.strings.get(key)
        if entry is not None and entry[1] is not None and entry[1] <= self.clock.now:
            del self.strings[key]
            return None
        return entry

    def set(self, key, value, nx=False, px=None):
        self._hook("set", key)
        if nx and self._live(key) is not None:
            return None
        assert px is None or (isinstance(px, int) and px > 0)
        self.strings[key] = (value, None if px is None else self.clock.now + px / 1000)
        return True

    def get(self, key):
        self._hook("get", key)
        entry = self._live(key)
        return None if entry is None else entry[0]

    def delete(self, *keys):
        self._hook("delete", *keys)
        return sum(1 for key in keys if self.strings.pop(key, None) is not None)

    def live_keys(self, fragment: str) -> list[str]:
        return sorted(key for key in list(self.strings) if fragment in key and self._live(key) is not None)


def _redis_driver(client, **kwargs):
    from hunt.queue.drivers.redis import RedisDriver

    return RedisDriver(prefix="t", client=client, **kwargs)


class TestRedisReplay:
    def test_completed_payload_pushed_again_is_not_run_by_another_worker(self, clock):
        client = ReplayFakeRedis(clock)
        _redis_driver(client).push(OkJob(1))
        payload = client.lists["t:default"][0]
        job_uid = open_payload(payload)["id"]

        assert _work(_redis_driver(client)) == 1
        assert Runs.log == [1]
        assert client.live_keys(":processed:") == [f"t:processed:{job_uid}"]
        assert client.live_keys(":running:") == []

        client.lpush("t:default", payload)  # the identical signed payload, pushed again
        clock.advance(3600)
        out = Output()
        assert _work(_redis_driver(client), out) == 1
        assert Runs.log == [1]
        assert out.count("already finished") == 1
        assert client.lists["t:default"] == [] and client.lists["t:default:processing"] == []
        assert client.zsets.get("t:default:reserved", {}) == {} and client.zsets.get("t:default:delayed", {}) == {}

    def test_only_set_get_and_del_are_used_for_the_markers(self, clock):
        client = ReplayFakeRedis(clock)
        driver = _redis_driver(client)
        driver.push(OkJob(1))
        assert _work(driver) == 1
        marker_calls = [
            call for call in client.calls if any(":running:" in str(a) or ":processed:" in str(a) for a in call)
        ]
        assert {call[0] for call in marker_calls} == {"set", "get", "delete"}

    def test_job_is_marked_processed_before_it_leaves_custody(self, clock):
        """A crash between the two steps must leave a redelivery that is recognised as finished."""
        client = ReplayFakeRedis(clock)
        driver = _redis_driver(client, retry_after=60)
        driver.push(OkJob(1))

        def crash():
            raise KeyboardInterrupt

        job = driver.pop()
        job_uid = open_payload(job["payload"])["id"]
        assert driver.claim_run(job_uid, job) == "claimed"
        client.before["lrem"] = crash  # dies while removing the job from the processing list
        with pytest.raises(KeyboardInterrupt):
            driver.complete(job["id"], job_uid)
        assert client.lists["t:default:processing"] != []

        clock.advance(61)
        out = Output()
        assert _work(_redis_driver(client, retry_after=60), out) == 1
        assert Runs.log == []  # the handler is not run for the redelivery
        assert out.count("already finished") == 1
        assert client.lists["t:default:processing"] == []

    def test_crash_before_completion_is_redelivered_and_runs(self, clock):
        client = ReplayFakeRedis(clock)
        crashed = _redis_driver(client, retry_after=60)
        crashed.push(OkJob(4))
        job = crashed.pop()
        job_uid = open_payload(job["payload"])["id"]
        assert crashed.claim_run(job_uid, job) == "claimed"
        # ... the worker dies here.

        survivor = _redis_driver(client, retry_after=60)
        assert _work(survivor) == 0
        clock.advance(60.5)
        assert _work(survivor) == 1
        assert Runs.log == [4]
        assert client.live_keys(":processed:") == [f"t:processed:{job_uid}"]

    def test_duplicate_of_a_running_job_is_postponed_then_dropped(self, clock):
        client = ReplayFakeRedis(clock)
        first, second = _redis_driver(client), _redis_driver(client)
        first.push(HookJob(3))
        envelope = json.loads(client.lists["t:default"][0])
        envelope["uuid"] = "attacker-copy"  # same signed body, different envelope
        copy = json.dumps(envelope)
        second_out = Output()

        def duplicate_arrives_while_running():
            client.lpush("t:default", copy)
            assert _work(second, second_out) == 1
            assert copy in client.zsets["t:default:delayed"]  # put back
            assert client.hashes["t:attempts"]["attacker-copy"] == 0  # not counted as an attempt

        Runs.during = duplicate_arrives_while_running
        assert _work(first) == 1
        assert Runs.log == [("start", 3), ("end", 3)]
        assert second_out.count("being run by another worker") == 1

        clock.advance(10)
        assert _work(second, second_out) == 1
        assert Runs.log == [("start", 3), ("end", 3)]
        assert second_out.count("already finished") == 1
        assert client.zsets["t:default:delayed"] == {} and client.lists["t:default"] == []

    def test_two_workers_finishing_the_same_id_record_one_completion(self, clock):
        client = ReplayFakeRedis(clock)
        first, second = _redis_driver(client, retry_after=60), _redis_driver(client, retry_after=60)
        first.push(HookJob(7))
        second_out = Output()

        def other_worker_takes_over():
            clock.advance(61)
            assert _work(second, second_out) == 1

        Runs.during = other_worker_takes_over
        first_out = Output()
        assert _work(first, first_out) == 1
        assert second_out.count("Processed:") == 1 and first_out.count("Processed:") == 0
        assert first_out.count("also completed by another worker") == 1
        assert len(client.live_keys(":processed:")) == 1

    def test_released_job_runs_again_with_the_same_id_and_is_recorded_once(self, clock):
        client = ReplayFakeRedis(clock)
        Runs.flaky_failures = 1
        _redis_driver(client).push(FlakyJob())
        job_uid = open_payload(client.lists["t:default"][0])["id"]

        assert _work(_redis_driver(client)) == 1
        assert Runs.log == ["flaky-failed"]
        assert client.live_keys(":running:") == [] and client.live_keys(":processed:") == []

        clock.advance(10)
        assert _work(_redis_driver(client)) == 1
        assert Runs.log == ["flaky-failed", "flaky-ok"]
        assert client.live_keys(":processed:") == [f"t:processed:{job_uid}"]
        assert client.live_keys(":running:") == []

    def test_copy_of_a_failed_job_is_not_run_but_retry_runs_it_once(self, queue_db, clock, monkeypatch):
        from hunt.console.commands import queue_failed

        client = ReplayFakeRedis(clock)
        Runs.flaky_failures = 1
        _redis_driver(client).push(OneShotJob())
        assert _work(_redis_driver(client)) == 1
        assert Runs.log == ["oneshot-failed"]
        failed = queue_db.raw("SELECT * FROM jobs_failed").fetchone()
        assert failed.connection == "redis"

        client.lpush("t:default", failed.payload)  # copied out of jobs_failed
        out = Output()
        assert _work(_redis_driver(client), out) == 1
        assert Runs.log == ["oneshot-failed"]
        assert out.count("already finished") == 1

        monkeypatch.setattr(queue_failed, "raw", queue_db.raw)
        monkeypatch.setattr(queue_failed, "_driver_for", lambda name: _redis_driver(client))
        assert queue_failed._retry_row(failed) is True  # what queue:retry and the admin retry call
        assert _count(queue_db, "jobs_failed") == 0
        assert _work(_redis_driver(client)) == 1
        assert Runs.log == ["oneshot-failed", "oneshot-ok"]
        assert _work(_redis_driver(client)) == 0

    def test_markers_expire_with_the_retention_window_and_the_lease_with_the_deadline(self, clock):
        client = ReplayFakeRedis(clock)
        driver = _redis_driver(client, retry_after=60, processed_retention=1000)
        driver.push(OkJob(1))
        job = driver.pop()
        job_uid = open_payload(job["payload"])["id"]
        assert driver.claim_run(job_uid, job) == "claimed"
        assert client.strings[f"t:running:{job_uid}"][1] == pytest.approx(clock.now + 60)
        assert _redis_driver(client).claim_run(job_uid, {}) == "busy"

        assert driver.complete(job["id"], job_uid) is True
        assert client.strings[f"t:processed:{job_uid}"][1] == pytest.approx(clock.now + 1000)
        assert _redis_driver(client).claim_run(job_uid, {}) == "done"
        clock.advance(999)
        assert client.live_keys(":processed:") != []
        clock.advance(2)
        assert client.live_keys(":processed:") == []
        assert driver.prune_processed() == 0  # nothing to do: Redis expires them

        forever = _redis_driver(client, processed_retention=0)
        assert forever._mark_processed("keep", "done:x", None) is True
        assert client.strings["t:processed:keep"][1] is None

    def test_stale_payload_is_rejected_on_redis_too(self, queue_db, clock):
        client = ReplayFakeRedis(clock)
        _redis_driver(client).push(OkJob(1))
        _redis_driver(client).later(30 * DAY, OkJob(2))
        clock.advance(DEFAULT_PROCESSED_RETENTION + 60)
        assert _work(_redis_driver(client)) == 1
        assert Runs.log == []
        assert "stale payload" in queue_db.raw("SELECT exception FROM jobs_failed").fetchone().exception

        clock.advance(30 * DAY - DEFAULT_PROCESSED_RETENTION)  # the delayed job is now due (by 60s)
        assert _work(_redis_driver(client)) == 1
        assert Runs.log == [2]

    def test_completion_retried_after_a_store_error_is_still_this_workers(self, clock):
        client = ReplayFakeRedis(clock)
        driver = _redis_driver(client)
        driver.push(OkJob(1))

        def flaky():
            raise ConnectionError("blip")

        out = Output()
        client.before["lrem"] = flaky  # the first attempt marks the id, then fails to drop custody
        assert _work(driver, out) == 1
        assert Runs.log == [1]
        assert out.count("Processed:") == 1 and out.count("also completed") == 0
        assert client.lists["t:default:processing"] == []


# ---------------------------------------------------------------------------
# Sync driver
# ---------------------------------------------------------------------------


class TestSyncDriver:
    def test_a_job_dispatched_once_runs_once(self):
        from hunt.queue.drivers.sync import SyncDriver

        driver = SyncDriver()
        driver.push(OkJob(1).chain([OkJob(2)]))
        driver.later(60, OkJob(3))
        assert Runs.log == [1, 2, 3]
        assert driver.pop() is None  # nothing is stored, so nothing can be replayed
