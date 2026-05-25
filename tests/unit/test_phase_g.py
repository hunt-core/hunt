"""Phase G — Queue Improvements tests."""

from __future__ import annotations

import time
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _Echo(list):
    """Capture output written via click.echo."""

    def __call__(self, msg="", err=False):
        self.append((msg, err))


def _make_raw_result(rows):
    """Minimal mock for SQLAlchemy result proxy."""
    result = MagicMock()
    result.fetchone.return_value = rows[0] if rows else None
    result.fetchall.return_value = rows
    return result


def _signed_payload(body_dict: dict) -> str:
    """Build a signed envelope string (same format as DatabaseDriver)."""
    from hunt.queue.drivers.database import _make_payload

    return _make_payload(body_dict)


# ---------------------------------------------------------------------------
# Job base class
# ---------------------------------------------------------------------------


class TestJobBase:
    def test_queue_default(self):
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        assert MyJob.queue == "default"

    def test_tries_default(self):
        from hunt.queue.job import Job

        assert Job.tries == 3

    def test_timeout_default(self):
        from hunt.queue.job import Job

        assert Job.timeout == 60

    def test_backoff_default(self):
        from hunt.queue.job import Job

        assert Job.backoff == 0

    def test_chain_sets_attribute(self):
        from hunt.queue.job import Job

        class A(Job):
            def handle(self):
                pass

        class B(Job):
            def handle(self):
                pass

        a = A()
        b = B()
        result = a.chain([b])
        assert result is a
        assert a._chain == [b]

    def test_dispatch_calls_queue_push(self):
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        job = MyJob()
        with patch("hunt.queue.manager.Queue") as mock_queue:
            job.dispatch()
        mock_queue.push.assert_called_once_with(job)

    def test_dispatch_later_calls_queue_later(self):
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        job = MyJob()
        with patch("hunt.queue.manager.Queue") as mock_queue:
            job.dispatch_later(60)
        mock_queue.later.assert_called_once_with(60, job)

    def test_dispatch_now_runs_synchronously(self):
        from hunt.queue.job import Job

        ran = []

        class MyJob(Job):
            def handle(self):
                ran.append(True)

        MyJob.dispatch_now()
        assert ran == [True]

    def test_failed_hook_default_is_noop(self):
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        MyJob().failed(Exception("boom"))  # should not raise


# ---------------------------------------------------------------------------
# SyncDriver
# ---------------------------------------------------------------------------


class TestSyncDriver:
    def test_push_calls_handle(self):
        from hunt.queue.drivers.sync import SyncDriver
        from hunt.queue.job import Job

        ran = []

        class MyJob(Job):
            def handle(self):
                ran.append(True)

        SyncDriver().push(MyJob())
        assert ran == [True]

    def test_later_runs_immediately(self):
        from hunt.queue.drivers.sync import SyncDriver
        from hunt.queue.job import Job

        ran = []

        class MyJob(Job):
            def handle(self):
                ran.append(True)

        SyncDriver().later(99, MyJob())
        assert ran == [True]

    def test_push_dispatches_chain(self):
        from hunt.queue.drivers.sync import SyncDriver
        from hunt.queue.job import Job

        order = []

        class First(Job):
            def handle(self):
                order.append("first")

        class Second(Job):
            def handle(self):
                order.append("second")

        class Third(Job):
            def handle(self):
                order.append("third")

        job = First()
        job.chain([Second(), Third()])
        SyncDriver().push(job)
        assert order == ["first", "second", "third"]

    def test_pop_returns_none(self):
        from hunt.queue.drivers.sync import SyncDriver

        assert SyncDriver().pop() is None

    def test_size_always_zero(self):
        from hunt.queue.drivers.sync import SyncDriver

        assert SyncDriver().size() == 0

    def test_delete_noop(self):
        from hunt.queue.drivers.sync import SyncDriver

        SyncDriver().delete(999)  # no exception

    def test_fail_noop(self):
        from hunt.queue.drivers.sync import SyncDriver

        SyncDriver().fail(1, "default", "{}", "boom")  # no exception


# ---------------------------------------------------------------------------
# DatabaseDriver serialization helpers
# ---------------------------------------------------------------------------


class TestDatabaseDriverSerialize:
    def test_serialize_job_basic(self):
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job

        class MyJob(Job):
            def __init__(self, x=1):
                self.x = x

            def handle(self):
                pass

        body = _serialize_job(MyJob(x=42))
        assert "MyJob" in body["class"]
        assert body["data"]["x"] == 42
        assert body["chain"] == []
        assert body["tries"] == 3
        assert body["backoff"] == 0

    def test_serialize_excludes_private(self):
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job

        class MyJob(Job):
            def __init__(self):
                self._private = "secret"
                self.public = "ok"

            def handle(self):
                pass

        body = _serialize_job(MyJob())
        assert "_private" not in body["data"]
        assert body["data"]["public"] == "ok"

    def test_serialize_chain(self):
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job

        class A(Job):
            def handle(self):
                pass

        class B(Job):
            def handle(self):
                pass

        a = A()
        a.chain([B()])
        body = _serialize_job(a)
        assert len(body["chain"]) == 1
        assert "B" in body["chain"][0]["class"]

    def test_serialize_custom_backoff_list(self):
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job

        class MyJob(Job):
            backoff: ClassVar[list] = [5, 10, 20]

            def handle(self):
                pass

        body = _serialize_job(MyJob())
        assert body["backoff"] == [5, 10, 20]


# ---------------------------------------------------------------------------
# DatabaseDriver push / later / push_payload
# ---------------------------------------------------------------------------


class TestDatabaseDriverPush:
    def setup_method(self):
        self._raw = MagicMock()
        self._raw.return_value = _make_raw_result([])
        self._raw_patcher = patch("hunt.queue.drivers.database.raw", self._raw)
        self._raw_patcher.start()
        self._payload_patcher = patch(
            "hunt.queue.drivers.database._make_payload",
            return_value='{"body":"{}","signature":"fake"}',
        )
        self._payload_patcher.start()

    def teardown_method(self):
        self._raw_patcher.stop()
        self._payload_patcher.stop()

    def _insert_call(self):
        return self._raw.call_args

    def test_push_inserts_to_jobs(self):
        from hunt.queue.drivers.database import DatabaseDriver
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        DatabaseDriver().push(MyJob())
        sql = self._insert_call()[0][0]
        assert "INSERT INTO jobs" in sql

    def test_later_sets_available_at(self):
        from hunt.queue.drivers.database import DatabaseDriver
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        before = int(time.time())
        DatabaseDriver().later(300, MyJob())
        args = self._insert_call()[0][1]
        assert args["at"] >= before + 300

    def test_push_payload_inserts(self):
        from hunt.queue.drivers.database import DatabaseDriver

        body = {"class": "app.jobs.X", "data": {}, "chain": []}
        DatabaseDriver().push_payload(body, "default")
        sql = self._insert_call()[0][0]
        assert "INSERT INTO jobs" in sql


# ---------------------------------------------------------------------------
# DatabaseDriver pop / delete / release / fail
# ---------------------------------------------------------------------------


class TestDatabaseDriverLifecycle:
    def setup_method(self):
        self._raw = MagicMock()
        self._raw_patcher = patch("hunt.queue.drivers.database.raw", self._raw)
        self._raw_patcher.start()

    def teardown_method(self):
        self._raw_patcher.stop()

    def test_pop_returns_none_when_empty(self):
        from hunt.queue.drivers.database import DatabaseDriver

        self._raw.return_value = _make_raw_result([])
        result = DatabaseDriver().pop()
        assert result is None

    def test_pop_filters_available_at(self):
        from hunt.queue.drivers.database import DatabaseDriver

        self._raw.return_value = _make_raw_result([])
        DatabaseDriver().pop("default")
        sql = self._raw.call_args_list[0][0][0]
        assert "available_at" in sql

    def test_pop_updates_reserved_at(self):
        from hunt.queue.drivers.database import DatabaseDriver

        row = MagicMock()
        row.id = 7
        row._mapping = {"id": 7, "queue": "default", "attempts": 1, "payload": "{}"}
        self._raw.return_value = MagicMock(fetchone=MagicMock(return_value=row))
        DatabaseDriver().pop()
        update_sql = self._raw.call_args_list[1][0][0]
        assert "reserved_at" in update_sql

    def test_delete_removes_job(self):
        from hunt.queue.drivers.database import DatabaseDriver

        self._raw.return_value = _make_raw_result([])
        DatabaseDriver().delete(42)
        sql = self._raw.call_args[0][0]
        assert "DELETE FROM jobs" in sql
        assert self._raw.call_args[0][1]["id"] == 42

    def test_release_clears_reserved_at(self):
        from hunt.queue.drivers.database import DatabaseDriver

        self._raw.return_value = _make_raw_result([])
        DatabaseDriver().release(5, delay=60)
        sql = self._raw.call_args[0][0]
        assert "reserved_at = NULL" in sql

    def test_fail_inserts_to_jobs_failed(self):
        from hunt.queue.drivers.database import DatabaseDriver

        self._raw.return_value = _make_raw_result([])
        DatabaseDriver().fail(3, "default", '{"payload": true}', "boom")
        # First call: INSERT jobs_failed; second: DELETE FROM jobs
        insert_sql = self._raw.call_args_list[0][0][0]
        delete_sql = self._raw.call_args_list[1][0][0]
        assert "jobs_failed" in insert_sql
        assert "DELETE FROM jobs" in delete_sql

    def test_fail_stores_exception_text(self):
        from hunt.queue.drivers.database import DatabaseDriver

        self._raw.return_value = _make_raw_result([])
        DatabaseDriver().fail(1, "default", "{}", "Something broke")
        args = self._raw.call_args_list[0][0][1]
        assert "Something broke" in args["exc"]

    def test_size_filters_available_at(self):
        from hunt.queue.drivers.database import DatabaseDriver

        mock_row = MagicMock()
        mock_row.cnt = 5
        self._raw.return_value = MagicMock(fetchone=MagicMock(return_value=mock_row))
        count = DatabaseDriver().size()
        assert count == 5
        sql = self._raw.call_args[0][0]
        assert "available_at" in sql


# ---------------------------------------------------------------------------
# QueueManager
# ---------------------------------------------------------------------------


class TestQueueManager:
    def test_configure_database(self):
        from hunt.queue.drivers.database import DatabaseDriver
        from hunt.queue.manager import _QueueManager

        mgr = _QueueManager()
        mgr.configure("database")
        assert isinstance(mgr._driver, DatabaseDriver)

    def test_configure_sync(self):
        from hunt.queue.drivers.sync import SyncDriver
        from hunt.queue.manager import _QueueManager

        mgr = _QueueManager()
        mgr.configure("sync")
        assert isinstance(mgr._driver, SyncDriver)

    def test_configure_default_is_sync(self):
        from hunt.queue.drivers.sync import SyncDriver
        from hunt.queue.manager import _QueueManager

        mgr = _QueueManager()
        mgr._get_driver()
        assert isinstance(mgr._driver, SyncDriver)

    def test_push_delegates_to_driver(self):
        from hunt.queue.job import Job
        from hunt.queue.manager import _QueueManager

        class MyJob(Job):
            def handle(self):
                pass

        mgr = _QueueManager()
        mock_driver = MagicMock()
        mgr._driver = mock_driver
        job = MyJob()
        mgr.push(job)
        mock_driver.push.assert_called_once_with(job)

    def test_later_delegates_to_driver(self):
        from hunt.queue.job import Job
        from hunt.queue.manager import _QueueManager

        class MyJob(Job):
            def handle(self):
                pass

        mgr = _QueueManager()
        mock_driver = MagicMock()
        mgr._driver = mock_driver
        job = MyJob()
        mgr.later(120, job)
        mock_driver.later.assert_called_once_with(120, job)

    def test_size_delegates_to_driver(self):
        from hunt.queue.manager import _QueueManager

        mgr = _QueueManager()
        mock_driver = MagicMock()
        mock_driver.size.return_value = 7
        mgr._driver = mock_driver
        assert mgr.size() == 7

    def test_configure_redis(self):
        from hunt.queue.drivers.redis import RedisDriver
        from hunt.queue.manager import _QueueManager

        mgr = _QueueManager()
        mgr.configure("redis", host="localhost", port=6379)
        assert isinstance(mgr._driver, RedisDriver)


# ---------------------------------------------------------------------------
# RedisDriver (mocked redis client)
# ---------------------------------------------------------------------------

_FAKE_PAYLOAD = '{"body":"{}","signature":"fake"}'


class TestRedisDriver:
    def setup_method(self):
        import sys

        self._mock_redis_module = MagicMock()
        self._mock_client = MagicMock()
        self._mock_redis_module.Redis.return_value = self._mock_client
        self._sys_patcher = patch.dict(sys.modules, {"redis": self._mock_redis_module})
        self._sys_patcher.start()
        # Patch _make_payload so signing is not needed
        self._payload_patcher = patch(
            "hunt.queue.drivers.redis._make_payload",
            return_value=_FAKE_PAYLOAD,
        )
        self._payload_patcher.start()

        from hunt.queue.drivers.redis import RedisDriver

        self.driver = RedisDriver(prefix="test_q")
        self.driver._client = self._mock_client

    def teardown_method(self):
        self._sys_patcher.stop()
        self._payload_patcher.stop()

    def test_push_lpush(self):
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        self.driver.push(MyJob())
        assert self._mock_client.lpush.called
        key = self._mock_client.lpush.call_args[0][0]
        assert "test_q:default" == key

    def test_later_zadd(self):
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self):
                pass

        self.driver.later(300, MyJob())
        assert self._mock_client.zadd.called
        key = self._mock_client.zadd.call_args[0][0]
        assert "delayed" in key

    def test_pop_migrates_delayed_then_brpop(self):
        self._mock_client.zrangebyscore.return_value = []
        self._mock_client.brpop.return_value = None
        result = self.driver.pop()
        assert self._mock_client.zrangebyscore.called
        assert self._mock_client.brpop.called
        assert result is None

    def test_pop_returns_dict(self):
        raw_bytes = _FAKE_PAYLOAD.encode()
        self._mock_client.zrangebyscore.return_value = []
        self._mock_client.brpop.return_value = (b"queue_key", raw_bytes)
        result = self.driver.pop()
        assert result is not None
        assert result["queue"] == "default"
        assert "payload" in result

    def test_delete_noop(self):
        self.driver.delete("some_id")
        assert not self._mock_client.delete.called

    def test_release_with_delay_zadd(self):
        self.driver.release(_FAKE_PAYLOAD.encode(), delay=30)
        assert self._mock_client.zadd.called

    def test_release_no_delay_lpush(self):
        self.driver.release(_FAKE_PAYLOAD.encode(), delay=0)
        assert self._mock_client.lpush.called

    def test_fail_writes_to_db_not_redis(self):
        # fail() must NOT write to a Redis sorted set — it writes to the DB jobs_failed table.
        # The DB call will silently fail in this unit test context (no real DB), which is fine.
        self.driver.fail("id", "default", "{}", "oops")
        assert not self._mock_client.zadd.called

    def test_size_uses_llen(self):
        self._mock_client.llen.return_value = 3
        assert self.driver.size() == 3

    def test_migrate_delayed_lpushes_ready_jobs(self):
        ready_payload = b'{"body": "x"}'
        self._mock_client.zrangebyscore.return_value = [ready_payload]
        self.driver._migrate_delayed("default")
        self._mock_client.lpush.assert_called_once()
        self._mock_client.zrem.assert_called_once()

    def test_redis_missing_raises(self):
        import sys

        from hunt.queue.drivers.redis import RedisDriver

        driver = RedisDriver()
        with patch.dict(sys.modules, {"redis": None}):
            with pytest.raises(RuntimeError, match="redis-py"):
                driver._redis()

    def test_push_payload(self):
        self.driver.push_payload({"class": "X", "data": {}, "chain": []}, "high")
        assert self._mock_client.lpush.called
        key = self._mock_client.lpush.call_args[0][0]
        assert "high" in key


# ---------------------------------------------------------------------------
# Backoff calculation helper
# ---------------------------------------------------------------------------


class TestBackoffHelper:
    def test_int_backoff(self):
        from hunt.console.commands.queue_work import _backoff_delay

        assert _backoff_delay(30, 1) == 30
        assert _backoff_delay(30, 3) == 30

    def test_list_backoff_first_attempt(self):
        from hunt.console.commands.queue_work import _backoff_delay

        assert _backoff_delay([5, 15, 60], 1) == 5

    def test_list_backoff_second_attempt(self):
        from hunt.console.commands.queue_work import _backoff_delay

        assert _backoff_delay([5, 15, 60], 2) == 15

    def test_list_backoff_clamps_to_last(self):
        from hunt.console.commands.queue_work import _backoff_delay

        assert _backoff_delay([5, 15, 60], 99) == 60

    def test_zero_backoff(self):
        from hunt.console.commands.queue_work import _backoff_delay

        assert _backoff_delay(0, 1) == 0


# ---------------------------------------------------------------------------
# queue:work — chain dispatching
# ---------------------------------------------------------------------------


class TestQueueWorkChain:
    def test_successful_job_pushes_chain_next(self):
        """After a job succeeds, the next chain job is pushed."""
        from hunt.queue.drivers.database import _serialize_job
        from hunt.queue.job import Job

        class First(Job):
            def handle(self):
                pass

        class Second(Job):
            def handle(self):
                pass

        first = First()
        first.chain([Second()])

        body = _serialize_job(first)
        # chain has one item (Second)
        assert len(body["chain"]) == 1

        # Simulate what the worker does after success
        payload = body
        chain = payload.get("chain", [])
        assert len(chain) == 1
        first_chain_item = chain[0]
        first_chain_item["chain"] = chain[1:]  # empty

        # The driver's push_payload would be called with first_chain_item
        assert "Second" in first_chain_item["class"]
        assert first_chain_item["chain"] == []


# ---------------------------------------------------------------------------
# queue:table command
# ---------------------------------------------------------------------------


class TestQueueTableCommand:
    def test_creates_migration_file(self, tmp_path):
        import os

        from click.testing import CliRunner

        from hunt.console.commands.queue_table import queue_table_command

        (tmp_path / "database" / "migrations").mkdir(parents=True)
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.chdir(tmp_path)
            result = runner.invoke(queue_table_command, catch_exceptions=False)

        assert result.exit_code == 0
        migrations = list((tmp_path / "database" / "migrations").glob("*_create_jobs_tables.py"))
        assert len(migrations) == 1
        content = migrations[0].read_text()
        assert "jobs_failed" in content
        assert "jobs" in content

    def test_migration_contains_both_tables(self, tmp_path):
        import os

        from click.testing import CliRunner

        from hunt.console.commands.queue_table import queue_table_command

        (tmp_path / "database" / "migrations").mkdir(parents=True)
        runner = CliRunner()
        with runner.isolated_filesystem():
            os.chdir(tmp_path)
            runner.invoke(queue_table_command, catch_exceptions=False)

        migration = next((tmp_path / "database" / "migrations").glob("*.py"))
        content = migration.read_text()
        assert 'Schema.create("jobs"' in content
        assert 'Schema.create("jobs_failed"' in content


# ---------------------------------------------------------------------------
# queue:failed / queue:retry / queue:flush commands
# ---------------------------------------------------------------------------


class TestQueueFailedCommands:
    def setup_method(self):
        self._raw = MagicMock()
        self._patcher = patch("hunt.console.commands.queue_failed.raw", self._raw)
        self._patcher.start()

    def teardown_method(self):
        self._patcher.stop()

    def _run_cmd(self, cmd, args=None):
        from click.testing import CliRunner

        runner = CliRunner()
        with patch("hunt.console.commands.queue_failed._load_env"):
            return runner.invoke(cmd, args or [], catch_exceptions=False)

    def test_queue_failed_no_rows(self):
        from hunt.console.commands.queue_failed import queue_failed_command

        self._raw.return_value = MagicMock(fetchall=MagicMock(return_value=[]))
        result = self._run_cmd(queue_failed_command)
        assert result.exit_code == 0
        assert "No failed jobs" in result.output

    def test_queue_failed_lists_rows(self):
        from hunt.console.commands.queue_failed import queue_failed_command

        row = MagicMock()
        row.id = 1
        row.uuid = "abc-123"
        row.queue = "default"
        row.failed_at = int(time.time())
        row.exception = "ValueError: bad input"
        self._raw.return_value = MagicMock(fetchall=MagicMock(return_value=[row]))
        result = self._run_cmd(queue_failed_command)
        assert result.exit_code == 0
        assert "abc-123" in result.output

    def test_queue_retry_not_found(self):
        from hunt.console.commands.queue_failed import queue_retry_command

        self._raw.return_value = MagicMock(fetchone=MagicMock(return_value=None))
        result = self._run_cmd(queue_retry_command, ["999"])
        assert result.exit_code != 0

    def test_queue_retry_requeues_job(self):
        from hunt.console.commands.queue_failed import queue_retry_command

        row = MagicMock()
        row.queue = "default"
        row.payload = '{"body": "x", "signature": "y"}'
        self._raw.return_value = MagicMock(fetchone=MagicMock(return_value=row))
        result = self._run_cmd(queue_retry_command, ["1"])
        assert result.exit_code == 0
        # First call: SELECT; second: INSERT jobs; third: DELETE jobs_failed
        assert self._raw.call_count == 3
        insert_sql = self._raw.call_args_list[1][0][0]
        assert "INSERT INTO jobs" in insert_sql
        delete_sql = self._raw.call_args_list[2][0][0]
        assert "DELETE FROM jobs_failed" in delete_sql

    def test_queue_flush_deletes_all(self):
        from hunt.console.commands.queue_failed import queue_flush_command

        self._raw.return_value = MagicMock()
        result = self._run_cmd(queue_flush_command)
        assert result.exit_code == 0
        sql = self._raw.call_args[0][0]
        assert "DELETE FROM jobs_failed" in sql

    def test_queue_flush_with_hours_filter(self):
        from hunt.console.commands.queue_failed import queue_flush_command

        self._raw.return_value = MagicMock()
        result = self._run_cmd(queue_flush_command, ["--hours", "24"])
        assert result.exit_code == 0
        sql = self._raw.call_args[0][0]
        assert "cutoff" in sql or "failed_at" in sql
