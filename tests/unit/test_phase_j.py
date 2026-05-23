"""Phase J — Testing Utilities tests."""
from __future__ import annotations

import asyncio
import datetime
import time
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


class _FakeUser:
    def __init__(self, user_id: int = 1, role: str = "user") -> None:
        self._attributes = {"id": user_id, "role": role}
        self._exists = True


# ===========================================================================
# 1. EventFake
# ===========================================================================

class TestEventFake:
    # ---- basic recording ---------------------------------------------------

    def test_sync_dispatch_recorded(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        class UserRegistered:
            pass

        with EventFake() as fake:
            Dispatcher.dispatch_sync(UserRegistered())
            fake.assert_dispatched(UserRegistered)

    def test_async_dispatch_recorded(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        class OrderPlaced:
            pass

        async def _go():
            with EventFake() as fake:
                await Dispatcher.dispatch(OrderPlaced())
                fake.assert_dispatched(OrderPlaced)

        _run(_go())

    def test_nothing_dispatched_initially(self):
        from hunt.testing.fakes import EventFake

        with EventFake() as fake:
            fake.assert_nothing_dispatched()

    # ---- count assertion ---------------------------------------------------

    def test_assert_dispatched_count_exact(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        class Ping:
            pass

        with EventFake() as fake:
            Dispatcher.dispatch_sync(Ping())
            Dispatcher.dispatch_sync(Ping())
            fake.assert_dispatched(Ping, count=2)

    def test_assert_dispatched_count_wrong_raises(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        class Ping:
            pass

        with EventFake() as fake:
            Dispatcher.dispatch_sync(Ping())
            with pytest.raises(AssertionError, match="Expected 3"):
                fake.assert_dispatched(Ping, count=3)

    # ---- assert_not_dispatched ---------------------------------------------

    def test_assert_not_dispatched_passes_when_absent(self):
        from hunt.testing.fakes import EventFake

        class NeverFired:
            pass

        with EventFake() as fake:
            fake.assert_not_dispatched(NeverFired)

    def test_assert_not_dispatched_fails_when_present(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        class Fired:
            pass

        with EventFake() as fake:
            Dispatcher.dispatch_sync(Fired())
            with pytest.raises(AssertionError, match="NOT to be dispatched"):
                fake.assert_not_dispatched(Fired)

    # ---- assert_dispatched failure -----------------------------------------

    def test_assert_dispatched_fails_when_not_fired(self):
        from hunt.testing.fakes import EventFake

        class Missing:
            pass

        with EventFake() as fake:
            with pytest.raises(AssertionError, match="Expected Missing to be dispatched"):
                fake.assert_dispatched(Missing)

    # ---- dispatched() accessor ---------------------------------------------

    def test_dispatched_returns_only_matching_instances(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        class A:
            def __init__(self, val): self.val = val

        class B:
            pass

        with EventFake() as fake:
            a1 = A(1)
            a2 = A(2)
            Dispatcher.dispatch_sync(a1)
            Dispatcher.dispatch_sync(B())
            Dispatcher.dispatch_sync(a2)
            found = fake.dispatched(A)
        assert len(found) == 2
        assert found[0].val == 1
        assert found[1].val == 2

    def test_dispatched_returns_empty_for_unseen_type(self):
        from hunt.testing.fakes import EventFake

        class X:
            pass

        with EventFake() as fake:
            assert fake.dispatched(X) == []

    # ---- isolation after context exit -------------------------------------

    def test_dispatcher_restored_after_context_exit(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.testing.fakes import EventFake

        with EventFake():
            pass

        # After exit the instance attribute should be gone; class method is back
        assert "dispatch_sync" not in Dispatcher.__dict__


# ===========================================================================
# 2. QueueFake
# ===========================================================================

class TestQueueFake:
    # ---- basic recording ---------------------------------------------------

    def test_push_recorded(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class SendEmail(Job):
            def handle(self): pass

        job = SendEmail()
        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(job)
            fake.assert_pushed(SendEmail)

    def test_nothing_pushed_initially(self):
        from hunt.testing.fakes import QueueFake

        with QueueFake() as fake:
            fake.assert_nothing_pushed()

    def test_assert_not_pushed(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class NeverPushed(Job):
            def handle(self): pass

        with QueueFake() as fake:
            fake.assert_not_pushed(NeverPushed)

    # ---- count assertion ---------------------------------------------------

    def test_assert_pushed_count(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class Sync(Job):
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(Sync())
            Queue.push(Sync())
            fake.assert_pushed(Sync, count=2)

    def test_assert_pushed_count_wrong_raises(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class Sync(Job):
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(Sync())
            with pytest.raises(AssertionError, match="Expected 5"):
                fake.assert_pushed(Sync, count=5)

    # ---- callback filter ---------------------------------------------------

    def test_assert_pushed_with_callback(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class ProcessOrder(Job):
            def __init__(self, order_id): self.order_id = order_id
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(ProcessOrder(1))
            Queue.push(ProcessOrder(42))
            fake.assert_pushed(ProcessOrder, callback=lambda j: j.order_id == 42)

    # ---- later / delay_for -------------------------------------------------

    def test_later_records_delay(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class DelayedJob(Job):
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            job = DelayedJob()
            Queue.later(300, job)
            fake.assert_pushed(DelayedJob)
            assert fake.delay_for(job) == 300

    def test_delay_for_unknown_job_returns_none(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class AnotherJob(Job):
            def handle(self): pass

        with QueueFake() as fake:
            job = AnotherJob()
            assert fake.delay_for(job) is None

    # ---- pushed() accessor -------------------------------------------------

    def test_pushed_returns_correct_instances(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class A(Job):
            def handle(self): pass

        class B(Job):
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(A())
            Queue.push(B())
            Queue.push(A())
            a_jobs = fake.pushed(A)
        assert len(a_jobs) == 2
        assert all(isinstance(j, A) for j in a_jobs)

    # ---- assert_not_pushed failure -----------------------------------------

    def test_assert_not_pushed_fails_when_present(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class ShouldNotBePushed(Job):
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(ShouldNotBePushed())
            with pytest.raises(AssertionError, match="NOT to be pushed"):
                fake.assert_not_pushed(ShouldNotBePushed)

    # ---- assert_nothing_pushed failure ------------------------------------

    def test_assert_nothing_pushed_fails_when_jobs_exist(self):
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class AJob(Job):
            def handle(self): pass

        with QueueFake() as fake:
            from hunt.queue.manager import Queue
            Queue.push(AJob())
            with pytest.raises(AssertionError, match="no jobs pushed"):
                fake.assert_nothing_pushed()


# ===========================================================================
# 3. freeze_time
# ===========================================================================

class TestFreezeTime:
    def test_time_time_frozen(self):
        from hunt.testing.fakes import freeze_time

        frozen = datetime.datetime(2026, 1, 1, 12, 0, 0)
        with freeze_time(frozen):
            assert time.time() == frozen.timestamp()

    def test_time_monotonic_frozen(self):
        from hunt.testing.fakes import freeze_time

        frozen = datetime.datetime(2026, 6, 15, 9, 30, 0)
        with freeze_time(frozen):
            assert time.monotonic() == frozen.timestamp()

    def test_datetime_now_frozen(self):
        from hunt.testing.fakes import freeze_time

        frozen = datetime.datetime(2025, 12, 31, 23, 59, 59)
        with freeze_time(frozen):
            assert datetime.datetime.now() == frozen

    def test_datetime_utcnow_frozen(self):
        from hunt.testing.fakes import freeze_time

        frozen = datetime.datetime(2024, 7, 4, 0, 0, 0)
        with freeze_time(frozen):
            assert datetime.datetime.utcnow() == frozen

    def test_yields_the_frozen_datetime(self):
        from hunt.testing.fakes import freeze_time

        target = datetime.datetime(2026, 3, 10, 8, 0, 0)
        with freeze_time(target) as dt:
            assert dt is target

    def test_defaults_to_now_when_none_passed(self):
        from hunt.testing.fakes import freeze_time

        before = time.time()
        with freeze_time() as frozen:
            assert frozen.timestamp() >= before

    def test_restored_after_context_exit(self):
        from hunt.testing.fakes import freeze_time

        frozen = datetime.datetime(2000, 1, 1)
        with freeze_time(frozen):
            pass
        # time.time() should no longer return the frozen value
        assert time.time() != frozen.timestamp()

    def test_datetime_now_returns_frozen_value(self):
        from hunt.testing.fakes import freeze_time

        frozen = datetime.datetime(2026, 1, 1, 12, 0, 0)
        with freeze_time(frozen):
            result = datetime.datetime.now()
            assert result == frozen


# ===========================================================================
# 4. Factory.state()
# ===========================================================================

class TestFactoryState:
    def _make_factory(self):
        """Build a simple test factory without needing a real Model."""
        from hunt.database.factory import Factory

        class FakeModel:
            @classmethod
            def create(cls, **kwargs):
                m = object.__new__(cls)
                m._attributes = kwargs
                m._exists = True
                return m

        class UserFactory(Factory):
            model = FakeModel
            states: ClassVar[dict] = {
                "admin": lambda: {"role": "admin"},
                "banned": lambda: {"banned_at": "2025-01-01", "active": False},
            }

            def definition(self):
                return {"name": "Alice", "role": "user", "active": True, "banned_at": None}

        return UserFactory()

    def test_make_without_state_uses_definition(self):
        factory = self._make_factory()
        user = factory.make()
        assert user._attributes["role"] == "user"
        assert user._attributes["active"] is True

    def test_state_overrides_definition(self):
        factory = self._make_factory()
        user = factory.state("admin").make()
        assert user._attributes["role"] == "admin"

    def test_state_merges_multiple_fields(self):
        factory = self._make_factory()
        user = factory.state("banned").make()
        assert user._attributes["active"] is False
        assert user._attributes["banned_at"] == "2025-01-01"

    def test_overrides_take_priority_over_state(self):
        factory = self._make_factory()
        user = factory.state("admin").make({"role": "superadmin"})
        assert user._attributes["role"] == "superadmin"

    def test_unknown_state_raises_value_error(self):
        factory = self._make_factory()
        with pytest.raises(ValueError, match="Unknown factory state"):
            factory.state("nonexistent")

    def test_state_returns_self_for_chaining(self):
        factory = self._make_factory()
        result = factory.state("admin")
        assert result is factory

    def test_multiple_states_chained(self):
        factory = self._make_factory()
        user = factory.state("admin").state("banned").make()
        assert user._attributes["role"] == "admin"
        assert user._attributes["active"] is False

    def test_make_many_applies_state(self):
        factory = self._make_factory()
        users = factory.state("admin").make_many(3)
        assert len(users) == 3
        assert all(u._attributes["role"] == "admin" for u in users)

    def test_factory_instance_state_isolated(self):
        from hunt.database.factory import Factory

        class FakeModel:
            @classmethod
            def create(cls, **kwargs):
                m = object.__new__(cls)
                m._attributes = kwargs
                return m

        class TF(Factory):
            model = FakeModel
            states: ClassVar[dict] = {"vip": lambda: {"vip": True}}
            def definition(self): return {"vip": False}

        f1 = TF().state("vip")
        f2 = TF()  # fresh instance, no state

        assert f1.make()._attributes["vip"] is True
        assert f2.make()._attributes["vip"] is False


# ===========================================================================
# 5. HuntTestCase.acting_as()
# ===========================================================================

class TestActingAs:
    def _make_kernel(self, route_fn):
        """Build a minimal kernel with one GET / route."""
        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router

        router = Router()
        router.get("/", route_fn)
        return HttpKernel(router)

    def test_acting_as_exposes_user_via_auth(self):
        from hunt.auth.manager import Auth

        captured = {}

        def view():
            captured["user"] = Auth.user()
            from hunt.http.response import JsonResponse
            return JsonResponse({"ok": True})

        kernel = self._make_kernel(view)

        class TestCase(MagicMock):
            pass

        # Build a minimal HuntTestCase inline
        from hunt.testing.test_case import HuntTestCase

        class TC(HuntTestCase):
            pass

        tc = TC()
        tc.kernel = kernel
        user = _FakeUser(user_id=7)
        tc.acting_as(user)
        _run(tc.get("/"))

        assert captured["user"] is user

    def test_acting_as_check_returns_true(self):
        from hunt.auth.manager import Auth

        captured = {}

        def view():
            captured["check"] = Auth.check()
            captured["guest"] = Auth.guest()
            from hunt.http.response import JsonResponse
            return JsonResponse({})

        from hunt.testing.test_case import HuntTestCase
        tc = HuntTestCase()
        tc.kernel = self._make_kernel(view)
        tc.acting_as(_FakeUser())
        _run(tc.get("/"))

        assert captured["check"] is True
        assert captured["guest"] is False

    def test_acting_as_id_returned(self):
        from hunt.auth.manager import Auth

        captured = {}

        def view():
            captured["id"] = Auth.id()
            from hunt.http.response import JsonResponse
            return JsonResponse({})

        from hunt.testing.test_case import HuntTestCase
        tc = HuntTestCase()
        tc.kernel = self._make_kernel(view)
        tc.acting_as(_FakeUser(user_id=99))
        _run(tc.get("/"))

        assert captured["id"] == 99

    def test_acting_as_returns_self_for_chaining(self):
        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router
        from hunt.testing.test_case import HuntTestCase

        tc = HuntTestCase()
        tc.kernel = HttpKernel(Router())
        result = tc.acting_as(_FakeUser())
        assert result is tc


# ===========================================================================
# 6. HuntTestCase.without_middleware()
# ===========================================================================

class TestWithoutMiddleware:
    def _kernel_with_mw(self, middleware_class, route_fn):
        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router

        router = Router()
        router.get("/", route_fn)
        return HttpKernel(router, global_middleware=[middleware_class])

    def test_middleware_called_normally(self):
        from hunt.http.middleware import Middleware, Next
        from hunt.http.request import Request
        from hunt.http.response import Response

        called = []

        class TrackingMw(Middleware):
            async def handle(self, request: Request, next: Next) -> Response:
                called.append(True)
                return await next(request)

        def view():
            from hunt.http.response import JsonResponse
            return JsonResponse({})

        from hunt.testing.test_case import HuntTestCase
        tc = HuntTestCase()
        tc.kernel = self._kernel_with_mw(TrackingMw, view)
        _run(tc.get("/"))
        assert called  # middleware ran

    def test_without_middleware_skips_class(self):
        from hunt.http.middleware import Middleware, Next
        from hunt.http.request import Request
        from hunt.http.response import Response

        called = []

        class SkippedMw(Middleware):
            async def handle(self, request: Request, next: Next) -> Response:
                called.append(True)
                return await next(request)

        def view():
            from hunt.http.response import JsonResponse
            return JsonResponse({})

        from hunt.testing.test_case import HuntTestCase
        tc = HuntTestCase()
        tc.kernel = self._kernel_with_mw(SkippedMw, view)
        tc.without_middleware(SkippedMw)
        _run(tc.get("/"))
        assert not called  # middleware was skipped

    def test_without_middleware_only_skips_specified(self):
        from hunt.http.middleware import Middleware, Next
        from hunt.http.request import Request
        from hunt.http.response import Response

        calls = []

        class AlwaysRuns(Middleware):
            async def handle(self, request: Request, next: Next) -> Response:
                calls.append("always")
                return await next(request)

        class Skipped(Middleware):
            async def handle(self, request: Request, next: Next) -> Response:
                calls.append("skipped")
                return await next(request)

        def view():
            from hunt.http.response import JsonResponse
            return JsonResponse({})

        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router
        router = Router()
        router.get("/", view)
        kernel = HttpKernel(router, global_middleware=[AlwaysRuns, Skipped])

        from hunt.testing.test_case import HuntTestCase
        tc = HuntTestCase()
        tc.kernel = kernel
        tc.without_middleware(Skipped)
        _run(tc.get("/"))

        assert "always" in calls
        assert "skipped" not in calls

    def test_without_middleware_returns_self_for_chaining(self):
        from hunt.http.kernel import HttpKernel
        from hunt.http.router import Router
        from hunt.testing.test_case import HuntTestCase

        tc = HuntTestCase()
        tc.kernel = HttpKernel(Router())
        result = tc.without_middleware()
        assert result is tc

    def test_middleware_restored_across_calls(self):
        """Each request inside a test applies the filter independently."""
        from hunt.http.middleware import Middleware, Next
        from hunt.http.request import Request
        from hunt.http.response import Response

        calls = []

        class Counted(Middleware):
            async def handle(self, request: Request, next: Next) -> Response:
                calls.append(1)
                return await next(request)

        def view():
            from hunt.http.response import JsonResponse
            return JsonResponse({})

        from hunt.testing.test_case import HuntTestCase
        tc = HuntTestCase()
        tc.kernel = self._kernel_with_mw(Counted, view)
        tc.without_middleware(Counted)

        # Two requests — both skip middleware
        _run(tc.get("/"))
        _run(tc.get("/"))
        assert len(calls) == 0


# ===========================================================================
# 7. RefreshDatabase
# ===========================================================================

class TestRefreshDatabase:
    def test_setup_is_noop(self):
        from hunt.testing.test_case import RefreshDatabase

        rd = RefreshDatabase()
        rd.setup_method()  # must not raise

    def test_refresh_tables_defaults_to_empty(self):
        from hunt.testing.test_case import RefreshDatabase

        assert RefreshDatabase.refresh_tables == []

    def test_subclass_can_set_refresh_tables(self):
        from hunt.testing.test_case import RefreshDatabase

        class MyTest(RefreshDatabase):
            refresh_tables: ClassVar[list] = ["users", "posts"]

        assert MyTest.refresh_tables == ["users", "posts"]

    def test_teardown_cleans_specified_tables(self):
        """DELETE is called for each configured table."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import StaticPool

        from hunt.testing.test_case import RefreshDatabase

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO users VALUES (1)"))
            conn.commit()

        class MyTest(RefreshDatabase):
            refresh_tables: ClassVar[list] = ["users"]

        rd = MyTest()
        with patch("hunt.database.connection.connection", return_value=engine):
            rd.teardown_method()

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        assert count == 0

    def test_teardown_auto_detects_tables_via_inspect(self):
        """When refresh_tables is empty, all tables are discovered and cleaned."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.pool import StaticPool

        from hunt.testing.test_case import RefreshDatabase

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE posts (id INTEGER PRIMARY KEY)"))
            conn.execute(text("INSERT INTO posts VALUES (1)"))
            conn.commit()

        rd = RefreshDatabase()
        with patch("hunt.database.connection.connection", return_value=engine):
            rd.teardown_method()

        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM posts")).scalar()
        assert count == 0


# ===========================================================================
# 8. Testing __init__ exports
# ===========================================================================

class TestExports:
    def test_event_fake_exported(self):
        from hunt.testing import EventFake
        assert EventFake is not None

    def test_queue_fake_exported(self):
        from hunt.testing import QueueFake
        assert QueueFake is not None

    def test_freeze_time_exported(self):
        from hunt.testing import freeze_time
        assert freeze_time is not None

    def test_hunt_test_case_exported(self):
        from hunt.testing import HuntTestCase
        assert HuntTestCase is not None

    def test_refresh_database_exported(self):
        from hunt.testing import RefreshDatabase
        assert RefreshDatabase is not None
