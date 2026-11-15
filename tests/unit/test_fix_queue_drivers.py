"""Regression tests for the queue drivers and payload format (QUE-01/02/04/05/06/07, REDIS-L1).

The database driver runs against a real SQLite file. The Redis driver runs
against ``FakeRedis`` below, an in-memory implementation of exactly the
commands the driver issues (no Redis server or redis-py is available here).
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import threading
from datetime import date, datetime
from decimal import Decimal

import pytest

from hunt.queue.job import Job, JobSerializationError

APP_KEY = "k" * 40


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_log_files(monkeypatch):
    """Error paths here log through the framework logger.

    If an earlier test left ``Log`` configured with a relative file path, those
    messages would be written to ``storage/logs`` under the current directory
    (the repository root). Use the bare fallback logger for these tests.
    """
    from hunt.log.manager import Log

    monkeypatch.delenv("LOG_CHANNEL", raising=False)
    monkeypatch.setattr(Log, "_default_logger", None)


@pytest.fixture(autouse=True)
def _app_key(monkeypatch):
    monkeypatch.setenv("APP_KEY", APP_KEY)
    monkeypatch.delenv("QUEUE_MAX_JOB_AGE", raising=False)
    monkeypatch.delenv("QUEUE_RETRY_AFTER", raising=False)
    monkeypatch.delenv("QUEUE_PROCESSED_RETENTION", raising=False)


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
    conn.raw("CREATE TABLE widgets (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(255))")
    try:
        yield conn
    finally:
        for engine in list(conn._connections.values()):
            engine.dispose()
        conn._connections.clear()
        conn._connections.update(saved_connections)
        conn.configure(saved_config)


class Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def db_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("hunt.queue.drivers.database._now", lambda: int(clock.now))
    return clock


@pytest.fixture
def redis_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("hunt.queue.drivers.redis._now", lambda: clock.now)
    return clock


class FakeRedis:
    """In-memory stand-in implementing the exact Redis commands the driver uses."""

    def __init__(self) -> None:
        self.lists: dict[str, list] = {}
        self.zsets: dict[str, dict] = {}
        self.hashes: dict[str, dict] = {}
        self.calls: list[tuple] = []
        # name -> callable invoked (once) just before that command runs
        self.before: dict[str, object] = {}

    def _hook(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        hook = self.before.pop(name, None)
        if hook is not None:
            hook()

    # lists -------------------------------------------------------------
    def lpush(self, key, *values):
        self._hook("lpush", key, *values)
        lst = self.lists.setdefault(key, [])
        for value in values:
            lst.insert(0, value)
        return len(lst)

    def blmove(self, src, dst, timeout, src_side="RIGHT", dst_side="LEFT"):
        self._hook("blmove", src, dst)
        assert (src_side, dst_side) == ("RIGHT", "LEFT")
        lst = self.lists.get(src) or []
        if not lst:
            return None
        value = lst.pop()
        self.lists.setdefault(dst, []).insert(0, value)
        return value

    def lrem(self, key, count, value):
        self._hook("lrem", key, count, value)
        lst = self.lists.get(key, [])
        removed = 0
        kept = []
        for item in lst:
            if item == value and (count == 0 or removed < count):
                removed += 1
            else:
                kept.append(item)
        self.lists[key] = kept
        return removed

    def lrange(self, key, start, stop):
        self._hook("lrange", key)
        assert (start, stop) == (0, -1)
        return list(self.lists.get(key, []))

    def llen(self, key):
        return len(self.lists.get(key, []))

    # sorted sets ---------------------------------------------------------
    def zadd(self, key, mapping, nx=False):
        self._hook("zadd", key)
        zset = self.zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member in zset:
                if not nx:
                    zset[member] = score
            else:
                zset[member] = score
                added += 1
        return added

    def zrem(self, key, *members):
        self._hook("zrem", key, *members)
        zset = self.zsets.get(key, {})
        return sum(1 for member in members if zset.pop(member, None) is not None)

    def zrangebyscore(self, key, low, high):
        self._hook("zrangebyscore", key)
        zset = self.zsets.get(key, {})
        return [m for m, s in sorted(zset.items(), key=lambda kv: kv[1]) if low <= s <= high]

    def zscore(self, key, member):
        return self.zsets.get(key, {}).get(member)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    # hashes --------------------------------------------------------------
    def hincrby(self, key, field, amount):
        bucket = self.hashes.setdefault(key, {})
        bucket[field] = bucket.get(field, 0) + amount
        return bucket[field]

    def hdel(self, key, *fields):
        bucket = self.hashes.get(key, {})
        return sum(1 for field in fields if bucket.pop(field, None) is not None)


class Recorder:
    ran: list = []  # noqa: RUF012


class SimpleJob(Job):
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def handle(self) -> None:
        Recorder.ran.append(self.value)


@pytest.fixture(autouse=True)
def _reset_recorder():
    Recorder.ran = []


def _redis_driver(client, **kwargs):
    from hunt.queue.drivers.redis import RedisDriver

    return RedisDriver(prefix="t", client=client, **kwargs)


# ---------------------------------------------------------------------------
# Signing API (QUE-04)
# ---------------------------------------------------------------------------


class TestSigning:
    def test_no_purpose_keeps_the_original_digest(self):
        from hunt.security.signing import sign, verify

        expected = hmac.new(APP_KEY.encode(), b"payload", hashlib.sha256).hexdigest()
        assert sign("payload") == expected
        assert sign("payload", purpose=None) == expected
        assert verify("payload", expected)

    def test_purpose_derives_a_separate_key(self):
        from hunt.security.signing import sign

        sub_key = hmac.new(APP_KEY.encode(), b"hunt.queue.v1", hashlib.sha256).digest()
        expected = hmac.new(sub_key, b"payload", hashlib.sha256).hexdigest()
        assert sign("payload", purpose="queue.v1") == expected
        assert sign("payload", purpose="queue.v1") != sign("payload")
        assert sign("payload", purpose="queue.v1") != sign("payload", purpose="email-verify")

    def test_signature_is_not_valid_for_another_purpose(self):
        from hunt.security.signing import sign, verify

        queue_sig = sign("payload", purpose="queue.v1")
        assert verify("payload", queue_sig, purpose="queue.v1")
        assert not verify("payload", queue_sig)
        assert not verify("payload", queue_sig, purpose="email-verify")
        assert not verify("payload", sign("payload"), purpose="queue.v1")

    def test_queue_payload_is_signed_for_the_queue_purpose(self):
        from hunt.queue.serialization import make_payload, open_payload
        from hunt.security.signing import verify

        envelope = json.loads(make_payload({"class": "x.Y", "data": {}}))
        assert verify(envelope["body"], envelope["signature"], purpose="queue.v1")
        assert not verify(envelope["body"], envelope["signature"])
        body = open_payload(json.dumps(envelope))
        assert body["class"] == "x.Y"
        assert isinstance(body["id"], str) and body["id"]
        assert isinstance(body["created_at"], int)

    def test_each_payload_gets_its_own_id(self):
        from hunt.queue.serialization import make_payload, open_payload

        first = open_payload(make_payload({"class": "x.Y"}))
        second = open_payload(make_payload({"class": "x.Y"}))
        assert first["id"] != second["id"]

    def test_legacy_and_tampered_payloads_are_rejected_with_a_reason(self):
        from hunt.queue.serialization import InvalidPayload, open_payload
        from hunt.security.signing import sign

        body = json.dumps({"class": "x.Y", "data": {}})
        legacy = json.dumps({"body": body, "signature": sign(body)})
        with pytest.raises(InvalidPayload, match="legacy queue signature"):
            open_payload(legacy)
        assert open_payload(legacy, allow_legacy=True)["class"] == "x.Y"

        with pytest.raises(InvalidPayload, match="signature mismatch"):
            open_payload(json.dumps({"body": body, "signature": "0" * 64}))
        with pytest.raises(InvalidPayload, match="signature mismatch"):
            open_payload(json.dumps({"body": body, "signature": "0" * 64}), allow_legacy=True)
        with pytest.raises(InvalidPayload, match="not valid JSON"):
            open_payload("not json at all")
        with pytest.raises(InvalidPayload, match="signature envelope"):
            open_payload(json.dumps({"class": "x.Y"}))

    def test_stale_payload_rejected_when_max_age_is_set(self, monkeypatch):
        from hunt.queue.serialization import InvalidPayload, make_payload, open_payload

        payload = make_payload({"class": "x.Y", "created_at": 1000})
        # By default a payload older than the processed-id retention is stale
        # (see test_fix_queue_replay.py); retention 0 means "no limit".
        with pytest.raises(InvalidPayload, match="QUEUE_PROCESSED_RETENTION"):
            open_payload(payload)
        assert open_payload(payload, retention=0)["created_at"] == 1000
        monkeypatch.setenv("QUEUE_MAX_JOB_AGE", "60")
        with pytest.raises(InvalidPayload, match="QUEUE_MAX_JOB_AGE"):
            open_payload(payload)


# ---------------------------------------------------------------------------
# Database driver against real SQLite (QUE-01, QUE-07)
# ---------------------------------------------------------------------------


class TestDatabaseDriverSQLite:
    def _driver(self, **kwargs):
        from hunt.queue.drivers.database import DatabaseDriver

        return DatabaseDriver(**kwargs)

    def test_push_pop_delete(self, queue_db, db_clock):
        driver = self._driver()
        driver.push(SimpleJob(1))
        assert driver.size() == 1
        job = driver.pop()
        assert job["attempts"] == 1
        assert job["reserved_at"] == int(db_clock.now)
        assert driver.pop() is None  # reserved, not yet stale
        assert driver.size() == 0
        driver.delete(job["id"])
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 0

    def test_job_of_a_dead_worker_is_reclaimed_after_retry_after(self, queue_db, db_clock):
        driver = self._driver(retry_after=90)
        driver.push(SimpleJob(1))
        first = driver.pop()
        assert first["attempts"] == 1
        # The worker dies here: no delete, release or fail.
        db_clock.advance(89)
        assert self._driver(retry_after=90).pop() is None
        db_clock.advance(2)
        other_worker = self._driver(retry_after=90)
        assert other_worker.size() == 1
        second = other_worker.pop()
        assert second is not None
        assert second["id"] == first["id"]
        assert second["attempts"] == 2
        assert second["reserved_at"] == int(db_clock.now)
        assert other_worker.pop() is None

    def test_retry_after_defaults_and_env(self, monkeypatch):
        assert self._driver().retry_after == 90
        monkeypatch.setenv("QUEUE_RETRY_AFTER", "300")
        assert self._driver().retry_after == 300
        assert self._driver(retry_after=10).retry_after == 10

    def test_manager_passes_retry_after(self):
        from hunt.queue.manager import _build_driver

        assert _build_driver("database", retry_after=120).retry_after == 120
        assert _build_driver("database", host="ignored").retry_after == 90

    def test_stale_claim_is_compare_and_swap(self, queue_db, db_clock, monkeypatch):
        """Two workers that both saw the same stale reservation: only one claims it."""
        import hunt.queue.drivers.database as dbmod

        driver_a = self._driver(retry_after=10)
        driver_b = self._driver(retry_after=10)
        driver_a.push(SimpleJob(1))
        assert driver_a.pop() is not None
        db_clock.advance(11)

        real_raw = dbmod.raw
        stolen = {}

        def racing_raw(sql, bindings=None, conn_name=None):
            # Just before A's claim UPDATE, worker B pops the same stale job.
            if sql.startswith("UPDATE jobs SET reserved_at = :now") and not stolen:
                stolen["job"] = None
                monkeypatch.setattr(dbmod, "raw", real_raw)
                db_clock.advance(1)
                stolen["job"] = driver_b.pop()
                monkeypatch.setattr(dbmod, "raw", racing_raw)
            return real_raw(sql, bindings, conn_name)

        monkeypatch.setattr(dbmod, "raw", racing_raw)
        got_a = driver_a.pop()
        assert stolen["job"] is not None
        assert stolen["job"]["attempts"] == 2
        assert got_a is None  # A lost the race and there is nothing else
        row = queue_db.raw("SELECT attempts FROM jobs").fetchone()
        assert row.attempts == 2  # incremented once for the reclaim, not twice

    def test_losing_a_claim_moves_on_to_the_next_job(self, queue_db, db_clock, monkeypatch):
        import hunt.queue.drivers.database as dbmod

        driver_a = self._driver()
        driver_b = self._driver()
        driver_a.push(SimpleJob(1))
        driver_a.push(SimpleJob(2))

        real_raw = dbmod.raw
        state = {}

        def racing_raw(sql, bindings=None, conn_name=None):
            if sql.startswith("UPDATE jobs SET reserved_at = :now") and not state:
                state["b"] = None
                monkeypatch.setattr(dbmod, "raw", real_raw)
                state["b"] = driver_b.pop()
                monkeypatch.setattr(dbmod, "raw", racing_raw)
            return real_raw(sql, bindings, conn_name)

        monkeypatch.setattr(dbmod, "raw", racing_raw)
        got_a = driver_a.pop()
        assert state["b"] is not None and got_a is not None
        assert got_a["id"] != state["b"]["id"]
        assert got_a["attempts"] == 1

    def test_release_makes_job_available_after_delay(self, queue_db, db_clock):
        driver = self._driver()
        driver.push(SimpleJob(1))
        job = driver.pop()
        driver.release(job["id"], delay=30)
        assert driver.pop() is None
        db_clock.advance(31)
        again = driver.pop()
        assert again["id"] == job["id"]
        assert again["attempts"] == 2

    def test_later_is_not_ready_until_due(self, queue_db, db_clock):
        driver = self._driver()
        driver.later(60, SimpleJob(1))
        assert driver.pop() is None
        db_clock.advance(61)
        assert driver.pop() is not None

    def test_fail_moves_job_in_one_transaction(self, queue_db, db_clock):
        driver = self._driver()
        driver.push(SimpleJob(1))
        job = driver.pop()
        driver.fail(job["id"], "default", job["payload"], "boom")
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 0
        failed = queue_db.raw("SELECT * FROM jobs_failed").fetchone()
        assert failed.connection == "database"
        assert failed.exception == "boom"
        assert failed.payload == job["payload"]

    def test_fail_rolls_back_when_the_second_statement_fails(self, queue_db, db_clock):
        """If the job row cannot be deleted, the jobs_failed insert must not persist."""
        driver = self._driver()
        driver.push(SimpleJob(1))
        job = driver.pop()
        queue_db.raw(
            "CREATE TRIGGER block_delete BEFORE DELETE ON jobs BEGIN SELECT RAISE(ABORT, 'delete blocked'); END"
        )
        with pytest.raises(Exception, match="delete blocked"):
            driver.fail(job["id"], "default", job["payload"], "boom")
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs_failed").fetchone().c == 0
        assert queue_db.raw("SELECT COUNT(*) AS c FROM jobs").fetchone().c == 1


# ---------------------------------------------------------------------------
# Redis driver against FakeRedis (QUE-01, QUE-02, QUE-07)
# ---------------------------------------------------------------------------


class TestRedisDriverFake:
    def test_pop_keeps_the_job_in_redis_until_deleted(self, redis_clock):
        client = FakeRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(1))
        job = driver.pop()
        assert job["attempts"] == 1
        assert json.loads(job["payload"])["uuid"]
        assert client.lists["t:default"] == []
        assert client.lists["t:default:processing"] == [job["id"]]
        assert client.zsets["t:default:reserved"][job["id"]] == redis_clock.now + driver.retry_after

        driver.delete(job["id"])
        assert client.lists["t:default:processing"] == []
        assert client.zsets["t:default:reserved"] == {}
        assert client.hashes["t:attempts"] == {}

    def test_job_of_a_dead_worker_is_redelivered_after_retry_after(self, redis_clock):
        client = FakeRedis()
        dead = _redis_driver(client, retry_after=90)
        dead.push(SimpleJob(1))
        first = dead.pop()
        assert first is not None
        # The worker dies: nothing else happens with `dead`.

        survivor = _redis_driver(client, retry_after=90)
        redis_clock.advance(89)
        assert survivor.pop() is None
        redis_clock.advance(2)
        second = survivor.pop()
        assert second is not None
        assert second["payload"] == first["payload"]
        assert second["attempts"] == 2
        assert client.lists["t:default:processing"] == [second["id"]]
        assert survivor.pop() is None

    def test_finished_job_is_not_redelivered(self, redis_clock):
        client = FakeRedis()
        driver = _redis_driver(client, retry_after=90)
        driver.push(SimpleJob(1))
        driver.delete(driver.pop()["id"])
        redis_clock.advance(500)
        assert driver.pop() is None
        assert _redis_driver(client, retry_after=90).pop() is None

    def test_crash_between_take_and_deadline_is_recovered(self, redis_clock):
        """A worker that dies right after BLMOVE leaves no deadline; the orphan scan adds one."""
        client = FakeRedis()
        dead = _redis_driver(client, retry_after=90)
        dead.push(SimpleJob(1))

        def crash():
            raise ConnectionError("worker killed")

        client.before["zadd"] = crash
        with pytest.raises(ConnectionError):
            dead.pop()
        assert len(client.lists["t:default:processing"]) == 1
        assert client.zsets.get("t:default:reserved", {}) == {}

        survivor = _redis_driver(client, retry_after=90)
        assert survivor.pop() is None  # scan gives the orphan a deadline
        assert len(client.zsets["t:default:reserved"]) == 1
        redis_clock.advance(91)
        job = survivor.pop()
        assert job is not None
        assert job["attempts"] == 1  # the first attempt never got as far as counting

    def test_delayed_job_is_delivered_once_when_two_workers_migrate(self, redis_clock):
        """QUE-02: both workers read the same due member; only one may enqueue it."""
        client = FakeRedis()
        worker_a = _redis_driver(client)
        worker_b = _redis_driver(client)
        worker_a.later(30, SimpleJob(1))
        redis_clock.advance(31)

        # Worker B runs its whole migration after A has read the due members
        # but before A has acted on them.
        client.before["lpush"] = lambda: worker_b._migrate_delayed("default")
        worker_a._migrate_delayed("default")

        assert len(client.lists["t:default"]) == 1
        assert client.lists["t:default:processing"] == []
        assert client.zsets["t:default:delayed"] == {}
        assert worker_a.pop() is not None
        assert worker_b.pop() is None

    def test_retried_job_is_delivered_once_with_two_workers(self, redis_clock):
        client = FakeRedis()
        worker_a = _redis_driver(client)
        worker_b = _redis_driver(client)
        worker_a.push(SimpleJob(1))
        job = worker_a.pop()
        worker_a.release(job["id"], delay=30)
        assert client.lists["t:default:processing"] == []
        assert client.zsets["t:default:reserved"] == {}
        assert worker_a.size() == 1  # delayed jobs count
        assert worker_a.pop() is None

        redis_clock.advance(31)
        client.before["zrem"] = lambda: worker_b._migrate_delayed("default")
        again = worker_a.pop()
        assert again is not None and again["attempts"] == 2
        assert worker_b.pop() is None
        assert client.lists["t:default"] == []

    def test_crash_during_migration_does_not_lose_the_job(self, redis_clock):
        client = FakeRedis()
        dead = _redis_driver(client, retry_after=90)
        dead.later(10, SimpleJob(1))
        redis_clock.advance(11)

        real_lpush = client.lpush
        pushes = []

        def dying_lpush(key, *values):
            pushes.append(key)
            if key == "t:default":
                raise ConnectionError("worker killed after ZREM")
            return real_lpush(key, *values)

        client.lpush = dying_lpush
        with pytest.raises(ConnectionError):
            dead._migrate_delayed("default")
        client.lpush = real_lpush
        # Removed from delayed, never reached the ready list — but still in custody.
        assert client.zsets["t:default:delayed"] == {}
        assert client.lists.get("t:default", []) == []
        assert len(client.lists["t:default:processing"]) == 1

        survivor = _redis_driver(client, retry_after=90)
        assert survivor.pop() is None
        redis_clock.advance(91)
        assert survivor.pop() is not None

    def test_expired_job_is_requeued_once_when_two_workers_reclaim(self, redis_clock):
        client = FakeRedis()
        dead = _redis_driver(client, retry_after=90)
        dead.push(SimpleJob(1))
        dead.pop()
        redis_clock.advance(91)

        worker_a = _redis_driver(client, retry_after=90)
        worker_b = _redis_driver(client, retry_after=90)
        client.before["lrange"] = lambda: worker_b._reclaim_expired("default")
        worker_a._reclaim_expired("default")
        assert len(client.lists["t:default"]) == 1
        assert client.lists["t:default:processing"] == []

    def test_stale_deadline_without_custody_is_dropped(self, redis_clock):
        client = FakeRedis()
        driver = _redis_driver(client, retry_after=90)
        client.zadd("t:default:reserved", {"ghost": redis_clock.now - 1})
        assert driver.pop() is None
        assert client.zsets["t:default:reserved"] == {}
        assert client.lists.get("t:default", []) == []

    def test_fail_records_in_database(self, queue_db, redis_clock):
        client = FakeRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(1))
        job = driver.pop()
        driver.fail(job["id"], "default", job["payload"], "boom")
        row = queue_db.raw("SELECT * FROM jobs_failed").fetchone()
        assert row.connection == "redis"
        assert row.exception == "boom"
        assert client.lists["t:default:processing"] == []
        assert client.zsets["t:default:reserved"] == {}
        assert client.lists.get("t:failed", []) == []

    def test_fail_falls_back_to_redis_list_when_database_is_down(self, monkeypatch, redis_clock):
        """QUE-07: a failed insert into jobs_failed must not make the job disappear."""
        conn = importlib.import_module("hunt.database.connection")

        def broken(*args, **kwargs):
            raise RuntimeError("database is down")

        monkeypatch.setattr(conn, "raw", broken)
        client = FakeRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(1))
        job = driver.pop()
        driver.fail(job["id"], "default", job["payload"], "boom")
        stored = [json.loads(item) for item in client.lists["t:failed"]]
        assert len(stored) == 1
        assert stored[0]["payload"] == job["payload"]
        assert stored[0]["exc"] == "boom"
        assert client.lists["t:default:processing"] == []

    def test_fail_keeps_custody_when_no_store_accepts_the_record(self, monkeypatch, redis_clock):
        conn = importlib.import_module("hunt.database.connection")

        def broken(*args, **kwargs):
            raise RuntimeError("database is down")

        monkeypatch.setattr(conn, "raw", broken)
        client = FakeRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(1))
        job = driver.pop()
        real_lpush = client.lpush

        def failing_lpush(key, *values):
            if key == "t:failed":
                raise ConnectionError("redis write failed")
            return real_lpush(key, *values)

        client.lpush = failing_lpush
        with pytest.raises(ConnectionError):
            driver.fail(job["id"], "default", job["payload"], "boom")
        assert client.lists["t:default:processing"] == [job["id"]]  # will be reclaimed, not lost

    def test_size_counts_ready_and_delayed(self, redis_clock):
        client = FakeRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(1))
        driver.later(60, SimpleJob(2))
        assert driver.size() == 2

    def test_job_popped_from_a_non_default_queue_is_cleaned_from_that_queue(self, redis_clock):
        """Custody is released on the queue the job was popped from, even for junk payloads."""
        client = FakeRedis()
        driver = _redis_driver(client)
        client.lpush("t:emails", "this is not json")
        job = driver.pop("emails")
        assert job["payload"] == "this is not json"
        driver.delete(job["id"])
        assert client.lists["t:emails:processing"] == []
        assert client.zsets["t:emails:reserved"] == {}

    def test_bytes_payloads_from_a_non_decoding_client(self, redis_clock):
        class BytesRedis(FakeRedis):
            def lpush(self, key, *values):
                return super().lpush(key, *[v.encode() if isinstance(v, str) else v for v in values])

        client = BytesRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(7))
        job = driver.pop()
        assert isinstance(job["id"], bytes)
        assert isinstance(job["payload"], str)
        driver.release(job["id"], delay=0)
        assert client.lists["t:default"] == [job["id"]]
        assert client.lists["t:default:processing"] == []

    def test_falls_back_to_brpoplpush_on_old_servers(self, redis_clock):
        class OldRedis(FakeRedis):
            def blmove(self, *args, **kwargs):
                raise RuntimeError("ERR unknown command 'BLMOVE'")

            def brpoplpush(self, src, dst, timeout):
                return FakeRedis.blmove(self, src, dst, timeout)

        client = OldRedis()
        driver = _redis_driver(client)
        driver.push(SimpleJob(1))
        assert driver.pop() is not None
        assert driver._blocking_move == "brpoplpush"

    def test_uses_the_shared_client_when_config_matches_the_environment(self, monkeypatch):
        """REDIS-L1: no private client when the queue points at the same server."""
        import hunt.redis_connection as shared
        from hunt.queue.drivers.redis import RedisDriver

        for name in ("REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        sentinel = FakeRedis()
        monkeypatch.setattr(shared, "get_redis", lambda: sentinel)
        assert RedisDriver()._redis() is sentinel
        assert RedisDriver(host="127.0.0.1", port=6379, db=0, password=None)._redis() is sentinel

    def test_uses_a_private_client_for_a_different_server(self, monkeypatch):
        import sys
        from unittest.mock import MagicMock

        import hunt.redis_connection as shared
        from hunt.queue.drivers.redis import RedisDriver

        for name in ("REDIS_HOST", "REDIS_PORT", "REDIS_DB", "REDIS_PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(shared, "get_redis", lambda: pytest.fail("shared client must not be used"))
        fake_module = MagicMock()
        monkeypatch.setitem(sys.modules, "redis", fake_module)
        driver = RedisDriver(host="queue.internal", db=3)
        assert driver._redis() is fake_module.Redis.return_value
        kwargs = fake_module.Redis.call_args.kwargs
        assert kwargs["host"] == "queue.internal" and kwargs["db"] == 3
        assert kwargs["socket_timeout"] >= 5


# ---------------------------------------------------------------------------
# Serialization contract and the sync driver (QUE-05, QUE-06)
# ---------------------------------------------------------------------------


class ExtraAttributeJob(Job):
    """Sets an attribute that is not an __init__ parameter."""

    def __init__(self, user_id: int = 0) -> None:
        self.user_id = user_id
        self.label = f"user-{user_id}"
        self.count = 0

    def handle(self) -> None:
        Recorder.ran.append((self.user_id, self.label, self.count))


class RichValuesJob(Job):
    def __init__(self, when: datetime | None = None, day: date | None = None, amount: Decimal | None = None) -> None:
        self.when = when
        self.day = day
        self.amount = amount
        self.nested = {"items": [when, amount]}

    def handle(self) -> None:
        Recorder.ran.append((self.when, self.day, self.amount, self.nested))


class HiddenArgumentJob(Job):
    """Keeps a required constructor argument only in a private attribute."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def handle(self) -> None:
        Recorder.ran.append(self._secret)


class CustomSerializedJob(Job):
    def __init__(self, handle_obj: object = None) -> None:
        self.handle_obj = handle_obj

    def serialize(self) -> dict:
        return {"token": "abc"}

    @classmethod
    def unserialize(cls, data: dict) -> CustomSerializedJob:
        return cls(handle_obj=("restored", data["token"]))

    def handle(self) -> None:
        Recorder.ran.append(self.handle_obj)


class AsyncJob(Job):
    def __init__(self, value: int = 0) -> None:
        self.value = value

    async def handle(self) -> None:
        import asyncio

        await asyncio.sleep(0)
        Recorder.ran.append(("async", self.value))


class ThreadNameJob(Job):
    def handle(self) -> None:
        Recorder.ran.append(threading.current_thread() is threading.main_thread())


class TestSerializationContract:
    def test_attribute_that_is_not_an_init_parameter_survives(self):
        from hunt.queue.serialization import make_payload, open_payload, rebuild_job, serialize_job

        job = ExtraAttributeJob(user_id=5)
        job.count = 3
        body = open_payload(make_payload(serialize_job(job)))
        rebuilt = rebuild_job(ExtraAttributeJob, body["data"])
        assert (rebuilt.user_id, rebuilt.label, rebuilt.count) == (5, "user-5", 3)

    def test_datetime_date_decimal_round_trip(self):
        from hunt.queue.serialization import round_trip

        when = datetime(2026, 1, 2, 3, 4, 5)
        rebuilt = round_trip(RichValuesJob(when=when, day=date(2026, 1, 2), amount=Decimal("19.99")))
        assert rebuilt.when == when
        assert rebuilt.day == date(2026, 1, 2)
        assert rebuilt.amount == Decimal("19.99")
        assert rebuilt.nested == {"items": [when, Decimal("19.99")]}

    def test_unserializable_attribute_is_named(self):
        from hunt.queue.serialization import serialize_job

        job = SimpleJob()
        job.connection = object()
        with pytest.raises(JobSerializationError, match=r"SimpleJob\.connection"):
            serialize_job(job)

        job = SimpleJob()
        job.options = {"callbacks": [1, {2, 3}]}
        with pytest.raises(JobSerializationError, match=r"SimpleJob\.options\['callbacks'\]\[1\]"):
            serialize_job(job)

    def test_missing_constructor_argument_is_explained(self):
        from hunt.queue.serialization import round_trip

        with pytest.raises(JobSerializationError, match="__init__ requires secret"):
            round_trip(HiddenArgumentJob("x"))

    def test_custom_serialize_hooks_are_used(self):
        from hunt.queue.serialization import round_trip, serialize_job

        job = CustomSerializedJob(handle_obj=object())
        assert serialize_job(job)["data"] == {"token": "abc"}
        assert round_trip(job).handle_obj == ("restored", "abc")

    def test_model_is_stored_as_identifier_and_refetched(self, queue_db):
        from hunt.database.model import Model
        from hunt.queue.serialization import make_payload, open_payload, rebuild_job, serialize_job

        global Widget

        class Widget(Model):
            table = "widgets"
            fillable = ["name"]  # noqa: RUF012

        class ModelJob(Job):
            def __init__(self, widget=None) -> None:
                self.widget = widget

            def handle(self) -> None:
                pass

        Widget.__qualname__ = "Widget"
        queue_db.raw("INSERT INTO widgets (name) VALUES ('first')")
        widget = Widget.find(1)
        assert widget is not None

        body = serialize_job(ModelJob(widget))
        assert body["data"]["widget"] == {"__hunt__": "model", "class": f"{__name__}.Widget", "key": 1}
        assert "first" not in make_payload(body)  # the row itself is not in the payload

        queue_db.raw("UPDATE widgets SET name = 'renamed' WHERE id = 1")
        rebuilt = rebuild_job(ModelJob, open_payload(make_payload(body))["data"])
        assert isinstance(rebuilt.widget, Widget)
        assert rebuilt.widget.name == "renamed"  # fresh from the database

        queue_db.raw("DELETE FROM widgets")
        with pytest.raises(LookupError, match="no longer exists"):
            rebuild_job(ModelJob, body["data"])

    def test_unsaved_model_is_rejected(self, queue_db):
        from hunt.database.model import Model
        from hunt.queue.serialization import serialize_job

        class Gadget(Model):
            table = "widgets"

        job = SimpleJob()
        job.gadget = Gadget()
        with pytest.raises(JobSerializationError, match="no primary key"):
            serialize_job(job)


class TestSyncDriverContract:
    def test_runs_a_rebuilt_copy(self):
        from hunt.queue.drivers.sync import SyncDriver

        job = ExtraAttributeJob(user_id=9)
        job.count = 2
        SyncDriver().push(job)
        assert Recorder.ran == [(9, "user-9", 2)]

    def test_rejects_what_a_real_driver_would_reject(self):
        from hunt.queue.drivers.sync import SyncDriver

        job = SimpleJob()
        job.socket = object()
        with pytest.raises(JobSerializationError, match=r"SimpleJob\.socket"):
            SyncDriver().push(job)
        assert Recorder.ran == []

        with pytest.raises(JobSerializationError):
            SyncDriver().push(HiddenArgumentJob("x"))

    def test_dispatch_from_a_worker_thread(self):
        """QUE-06: SIGALRM can only be armed on the main thread."""
        from hunt.queue.drivers.sync import SyncDriver

        errors: list[BaseException] = []

        def target():
            try:
                SyncDriver().push(ThreadNameJob())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(10)
        assert errors == []
        assert Recorder.ran == [False]

    def test_async_handle_is_awaited(self):
        from hunt.queue.drivers.sync import SyncDriver

        SyncDriver().push(AsyncJob(1))
        assert Recorder.ran == [("async", 1)]

    async def test_async_handle_is_awaited_inside_a_running_loop(self):
        from hunt.queue.drivers.sync import SyncDriver

        SyncDriver().push(AsyncJob(2))
        assert Recorder.ran == [("async", 2)]

    def test_dispatch_now_awaits_async_handle(self):
        AsyncJob.dispatch_now(value=3)
        assert Recorder.ran == [("async", 3)]

    def test_chain_runs_in_order(self):
        from hunt.queue.drivers.sync import SyncDriver

        first = SimpleJob(1)
        first.chain([SimpleJob(2), SimpleJob(3)])
        SyncDriver().push(first)
        assert Recorder.ran == [1, 2, 3]

    @pytest.mark.skipif(not hasattr(__import__("signal"), "SIGALRM"), reason="needs SIGALRM")
    def test_timeout_cannot_be_swallowed_by_job_code(self):
        from hunt.queue.drivers.sync import JobTimeoutError, SyncDriver

        global SwallowingJob

        class SwallowingJob(Job):
            timeout = 1

            def handle(self) -> None:
                import time

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        time.sleep(0.05)
                    except Exception:
                        pass  # a broad except must not hide the timeout

        with pytest.raises(JobTimeoutError):
            SyncDriver().push(SwallowingJob())
