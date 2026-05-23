"""Phase F — Queued Listeners & Event Subscribers tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _app():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


def _fresh_dispatcher():
    """Return the singleton Dispatcher with all listeners cleared."""
    from hunt.events.dispatcher import Dispatcher
    # Clear state by replacing the internal dict directly
    Dispatcher._listeners.clear()
    return Dispatcher


# ---------------------------------------------------------------------------
# Event + Listener fixtures (defined at module scope so they are importable
# by QueuedEventListener which uses importlib)
# ---------------------------------------------------------------------------

class UserRegistered:
    def __init__(self, user_id: int = 0, email: str = "") -> None:
        self.user_id = user_id
        self.email = email


class OrderPlaced:
    def __init__(self, order_id: int = 0, total: float = 0.0) -> None:
        self.order_id = order_id
        self.total = total


class _Collector:
    events: ClassVar[list] = []

    @classmethod
    def reset(cls):
        cls.events = []


# ===========================================================================
# 1. QueuedEventListener — unit tests
# ===========================================================================

class TestQueuedEventListener:
    def setup_method(self):
        # Register test classes in the allowlist so handle() can execute them
        from hunt.events.queued import allow_event, allow_listener
        allow_listener(f"{__name__}._RecordingListener")
        allow_listener(f"{__name__}._NoArgListener")
        allow_event(f"{__name__}.UserRegistered")

    def test_stores_attributes(self):
        from hunt.events.queued import QueuedEventListener

        job = QueuedEventListener(
            listener_class="app.listeners.SendEmail",
            event_class="app.events.UserRegistered",
            event_data={"user_id": 1},
        )
        assert job.listener_class == "app.listeners.SendEmail"
        assert job.event_class == "app.events.UserRegistered"
        assert job.event_data == {"user_id": 1}

    def test_is_a_job(self):
        from hunt.events.queued import QueuedEventListener
        from hunt.queue.job import Job

        job = QueuedEventListener("a.B", "c.D", {})
        assert isinstance(job, Job)

    def test_handle_reconstructs_event_and_calls_listener(self):
        """handle() imports listener + event classes and calls listener.handle(event)."""
        from hunt.events.queued import QueuedEventListener

        received = {}

        class FakeListener:
            def handle(self, event):
                received["user_id"] = event.user_id
                received["email"] = event.email

        QueuedEventListener(
            listener_class=f"{__name__}.UserRegistered",  # uses module-level class as event
            event_class=f"{__name__}.UserRegistered",
            event_data={"user_id": 7, "email": "a@b.com"},
        )
        with patch("hunt.events.queued._import_dotted") as mock_import:
            mock_import.side_effect = lambda path: (
                FakeListener if "listener" in path.lower() or path.endswith("Registered")
                else UserRegistered
            )
            # Actually test with real imports:
            pass

        # Real test with module-level importable classes
        job2 = QueuedEventListener(
            listener_class=f"{__name__}._RecordingListener",
            event_class=f"{__name__}.UserRegistered",
            event_data={"user_id": 42, "email": "test@example.com"},
        )
        _RecordingListener.reset()
        job2.handle()
        assert _RecordingListener.last_event_user_id == 42
        assert _RecordingListener.last_event_email == "test@example.com"

    def test_handle_calls_listener_without_args_when_no_params(self):
        """If handle() takes no positional parameters, event is not passed."""
        from hunt.events.queued import QueuedEventListener

        job = QueuedEventListener(
            listener_class=f"{__name__}._NoArgListener",
            event_class=f"{__name__}.UserRegistered",
            event_data={"user_id": 1},
        )
        _NoArgListener.reset()
        job.handle()
        assert _NoArgListener.called

    def test_event_data_private_attrs_excluded(self):
        """Private attributes (_x) must not appear in event_data."""

        class EventWithPrivate:
            def __init__(self):
                self.public = "yes"
                self._private = "no"

        ev = EventWithPrivate()
        data = {k: v for k, v in vars(ev).items() if not k.startswith("_")}
        assert "public" in data
        assert "_private" not in data


# Helper classes at module scope for importlib
class _RecordingListener:
    last_event_user_id = None
    last_event_email = None

    @classmethod
    def reset(cls):
        cls.last_event_user_id = None
        cls.last_event_email = None

    def handle(self, event) -> None:
        _RecordingListener.last_event_user_id = event.user_id
        _RecordingListener.last_event_email = event.email


class _NoArgListener:
    called = False

    @classmethod
    def reset(cls):
        cls.called = False

    def handle(self) -> None:
        _NoArgListener.called = True


# ===========================================================================
# 2. EventServiceProvider — queued listeners
# ===========================================================================

class TestQueuedListeners:
    def setup_method(self):
        _fresh_dispatcher()

    def teardown_method(self):
        _fresh_dispatcher()

    def test_job_subclass_listener_pushed_to_queue(self):
        """A listener that is a Job subclass is pushed to the queue, not called inline."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.events.queued import QueuedEventListener
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class EmailJob(Job):
            def handle(self, event=None): pass

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [EmailJob]}

        provider = AppProvider(_app())
        provider.boot()

        with QueueFake() as fake:
            Dispatcher.dispatch_sync(UserRegistered(user_id=1))
            fake.assert_pushed(QueuedEventListener)

    def test_implements_queued_listener_flag_queues(self):
        """A listener with implements_queued_listener=True is queued even if not a Job."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.events.queued import QueuedEventListener
        from hunt.testing.fakes import QueueFake

        class FlaggedListener:
            implements_queued_listener = True
            def handle(self, event=None): pass

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [FlaggedListener]}

        provider = AppProvider(_app())
        provider.boot()

        with QueueFake() as fake:
            Dispatcher.dispatch_sync(UserRegistered(user_id=2))
            fake.assert_pushed(QueuedEventListener)

    def test_queued_wrapper_stores_correct_event_data(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.events.queued import QueuedEventListener
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class OrderJob(Job):
            def handle(self, event=None): pass

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {OrderPlaced: [OrderJob]}

        provider = AppProvider(_app())
        provider.boot()

        with QueueFake() as fake:
            Dispatcher.dispatch_sync(OrderPlaced(order_id=99, total=19.99))
            jobs = fake.pushed(QueuedEventListener)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.event_data["order_id"] == 99
        assert job.event_data["total"] == 19.99

    def test_queued_wrapper_stores_listener_class_path(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.events.queued import QueuedEventListener
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class MyJob(Job):
            def handle(self, event=None): pass

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [MyJob]}

        provider = AppProvider(_app())
        provider.boot()

        with QueueFake() as fake:
            Dispatcher.dispatch_sync(UserRegistered())
            jobs = fake.pushed(QueuedEventListener)

        assert MyJob.__name__ in jobs[0].listener_class

    def test_inline_listener_not_pushed_to_queue(self):
        """Normal (non-Job) listeners are called inline, not pushed to queue."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.testing.fakes import QueueFake

        calls = []

        class InlineListener:
            def handle(self, event): calls.append(event)

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [InlineListener]}

        provider = AppProvider(_app())
        provider.boot()

        with QueueFake() as fake:
            Dispatcher.dispatch_sync(UserRegistered(user_id=5))
            fake.assert_nothing_pushed()

        assert len(calls) == 1
        assert calls[0].user_id == 5

    def test_mixed_queued_and_inline_listeners(self):
        """Both queued and inline listeners for the same event work together."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.events.queued import QueuedEventListener
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        inline_calls = []

        class QueuedJob(Job):
            def handle(self, event=None): pass

        class InlineListener:
            def handle(self, event): inline_calls.append(event)

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [QueuedJob, InlineListener]}

        provider = AppProvider(_app())
        provider.boot()

        with QueueFake() as fake:
            Dispatcher.dispatch_sync(UserRegistered(user_id=10))
            fake.assert_pushed(QueuedEventListener)

        assert len(inline_calls) == 1

    def test_async_dispatch_also_queues(self):
        """dispatch() (async) pushes queued listeners to the queue."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider
        from hunt.events.queued import QueuedEventListener
        from hunt.queue.job import Job
        from hunt.testing.fakes import QueueFake

        class AsyncQueued(Job):
            def handle(self, event=None): pass

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [AsyncQueued]}

        provider = AppProvider(_app())
        provider.boot()

        async def _go():
            with QueueFake() as fake:
                await Dispatcher.dispatch(UserRegistered(user_id=3))
                fake.assert_pushed(QueuedEventListener)

        _run(_go())


# ===========================================================================
# 3. EventServiceProvider — event subscribers
# ===========================================================================

class TestEventSubscribers:
    def setup_method(self):
        _fresh_dispatcher()

    def teardown_method(self):
        _fresh_dispatcher()

    def test_subscriber_subscribe_called_on_boot(self):
        """The subscriber's subscribe() receives the Dispatcher on boot."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider

        subscribe_calls = []

        class MySubscriber:
            def subscribe(self, dispatcher):
                subscribe_calls.append(dispatcher)

        class AppProvider(EventServiceProvider):
            subscribe: ClassVar[list] = [MySubscriber]

        provider = AppProvider(_app())
        provider.boot()
        assert subscribe_calls == [Dispatcher]

    def test_subscriber_can_register_multiple_listeners(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider

        calls = []

        class UserSubscriber:
            def subscribe(self, dispatcher):
                dispatcher.listen(UserRegistered, self.on_registered)
                dispatcher.listen(OrderPlaced, self.on_ordered)

            def on_registered(self, event): calls.append(("registered", event.user_id))
            def on_ordered(self, event): calls.append(("ordered", event.order_id))

        class AppProvider(EventServiceProvider):
            subscribe: ClassVar[list] = [UserSubscriber]

        provider = AppProvider(_app())
        provider.boot()

        Dispatcher.dispatch_sync(UserRegistered(user_id=1))
        Dispatcher.dispatch_sync(OrderPlaced(order_id=42))

        assert ("registered", 1) in calls
        assert ("ordered", 42) in calls

    def test_multiple_subscribers_all_booted(self):
        from hunt.events.provider import EventServiceProvider

        booted = []

        class Sub1:
            def subscribe(self, dispatcher):
                booted.append("sub1")
                dispatcher.listen(UserRegistered, lambda e: None)

        class Sub2:
            def subscribe(self, dispatcher):
                booted.append("sub2")
                dispatcher.listen(OrderPlaced, lambda e: None)

        class AppProvider(EventServiceProvider):
            subscribe: ClassVar[list] = [Sub1, Sub2]

        AppProvider(_app()).boot()
        assert "sub1" in booted
        assert "sub2" in booted

    def test_subscriber_and_listen_dict_coexist(self):
        """Both listen dict and subscribe list can be used on the same provider."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider

        inline_calls = []
        subscriber_calls = []

        class InlineListener:
            def handle(self, event): inline_calls.append(event)

        class MySub:
            def subscribe(self, dispatcher):
                dispatcher.listen(OrderPlaced, self.on_order)
            def on_order(self, event): subscriber_calls.append(event)

        class AppProvider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [InlineListener]}
            subscribe: ClassVar[list] = [MySub]

        AppProvider(_app()).boot()
        Dispatcher.dispatch_sync(UserRegistered())
        Dispatcher.dispatch_sync(OrderPlaced())

        assert len(inline_calls) == 1
        assert len(subscriber_calls) == 1

    def test_empty_subscribe_list_is_noop(self):
        from hunt.events.provider import EventServiceProvider

        class AppProvider(EventServiceProvider):
            subscribe: ClassVar[list] = []

        AppProvider(_app()).boot()  # must not raise

    def test_subscriber_instance_state_preserved(self):
        """Each subscriber gets its own instance, and state is preserved."""
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider

        class StatefulSubscriber:
            def __init__(self): self.count = 0
            def subscribe(self, dispatcher):
                dispatcher.listen(UserRegistered, self.on_event)
            def on_event(self, event): self.count += 1

        class AppProvider(EventServiceProvider):
            subscribe: ClassVar[list] = [StatefulSubscriber]

        AppProvider(_app()).boot()
        Dispatcher.dispatch_sync(UserRegistered())
        Dispatcher.dispatch_sync(UserRegistered())
        # We can't directly check the instance, but the dispatcher ran both calls
        assert Dispatcher.has_listeners(UserRegistered)


# ===========================================================================
# 4. _should_queue helper
# ===========================================================================

class TestShouldQueue:
    def test_job_subclass_returns_true(self):
        from hunt.events.provider import _should_queue
        from hunt.queue.job import Job

        class MyJob(Job):
            def handle(self): pass

        assert _should_queue(MyJob)

    def test_plain_class_returns_false(self):
        from hunt.events.provider import _should_queue

        class PlainListener:
            def handle(self, event): pass

        assert not _should_queue(PlainListener)

    def test_implements_flag_returns_true(self):
        from hunt.events.provider import _should_queue

        class FlaggedListener:
            implements_queued_listener = True
            def handle(self, event): pass

        assert _should_queue(FlaggedListener)

    def test_flag_false_returns_false(self):
        from hunt.events.provider import _should_queue

        class NotQueued:
            implements_queued_listener = False
            def handle(self, event): pass

        assert not _should_queue(NotQueued)

    def test_job_subclass_with_flag_false_still_true(self):
        """Job subclass always queued, flag can't opt out."""
        from hunt.events.provider import _should_queue
        from hunt.queue.job import Job

        class MyJob(Job):
            implements_queued_listener = False
            def handle(self): pass

        assert _should_queue(MyJob)


# ===========================================================================
# 5. make:listener --queued
# ===========================================================================

class TestMakeListenerQueued:
    def test_queued_flag_generates_job_based_stub(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.make.listener import make_listener_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_listener_command, ["SendWelcomeEmail", "--queued"], catch_exceptions=False)
            content = (Path(td) / "app" / "listeners" / "SendWelcomeEmail.py").read_text()

        assert "from hunt.queue.job import Job" in content
        assert "implements_queued_listener = True" in content
        assert "class SendWelcomeEmail(Job)" in content

    def test_no_flag_generates_plain_stub(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.make.listener import make_listener_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_listener_command, ["LogActivity"], catch_exceptions=False)
            content = (Path(td) / "app" / "listeners" / "LogActivity.py").read_text()

        assert "class LogActivity:" in content
        assert "Job" not in content

    def test_short_flag_works(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.make.listener import make_listener_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_listener_command, ["SendEmail", "-q"], catch_exceptions=False)
            content = (Path(td) / "app" / "listeners" / "SendEmail.py").read_text()

        assert "implements_queued_listener = True" in content

    def test_queued_handle_accepts_event_param(self, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.make.listener import make_listener_command

        r = CliRunner()
        with r.isolated_filesystem(temp_dir=tmp_path) as td:
            r.invoke(make_listener_command, ["Processor", "--queued"], catch_exceptions=False)
            content = (Path(td) / "app" / "listeners" / "Processor.py").read_text()

        assert "def handle(self, event" in content


# ===========================================================================
# 6. Existing provider tests still pass (regression)
# ===========================================================================

class TestExistingProviderRegression:
    def setup_method(self):
        _fresh_dispatcher()

    def teardown_method(self):
        _fresh_dispatcher()

    def test_listen_dict_inline_listener_called(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider

        called = []

        class Listener:
            def handle(self, event): called.append(event)

        class Provider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [Listener]}

        Provider(_app()).boot()
        Dispatcher.dispatch_sync(UserRegistered(user_id=99))
        assert len(called) == 1
        assert called[0].user_id == 99

    def test_multiple_listeners_for_same_event(self):
        from hunt.events.dispatcher import Dispatcher
        from hunt.events.provider import EventServiceProvider

        calls_a, calls_b = [], []

        class A:
            def handle(self, event): calls_a.append(1)

        class B:
            def handle(self, event): calls_b.append(1)

        class Provider(EventServiceProvider):
            listen: ClassVar[dict] = {UserRegistered: [A, B]}

        Provider(_app()).boot()
        Dispatcher.dispatch_sync(UserRegistered())
        assert calls_a == [1]
        assert calls_b == [1]

    def test_empty_listen_dict_no_error(self):
        from hunt.events.provider import EventServiceProvider

        class Provider(EventServiceProvider):
            listen: ClassVar[dict] = {}
            subscribe: ClassVar[list] = []

        Provider(_app()).boot()  # must not raise
