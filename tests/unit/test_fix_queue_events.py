"""Regression tests for the event dispatcher and queued listeners (EVT-01, QUE-05)."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from hunt.events.dispatcher import Event, _Dispatcher
from hunt.queue.job import Job


class Seen:
    items: ClassVar[list] = []


@pytest.fixture(autouse=True)
def _reset():
    Seen.items = []


class OrderPlaced(Event):
    def __init__(self, order_id: int = 0) -> None:
        self.order_id = order_id


class RushOrderPlaced(OrderPlaced):
    pass


class Unrelated(Event):
    def __init__(self, order_id: int = 0) -> None:
        self.order_id = order_id


# ---------------------------------------------------------------------------
# Async listeners through dispatch_sync
# ---------------------------------------------------------------------------


def test_async_listener_runs_when_dispatched_synchronously():
    dispatcher = _Dispatcher()

    async def listener(event):
        await asyncio.sleep(0)
        Seen.items.append(event.order_id)
        return "done"

    dispatcher.listen(OrderPlaced, listener)
    assert dispatcher.dispatch_sync(OrderPlaced(7)) == ["done"]
    assert Seen.items == [7]


async def test_async_listener_is_scheduled_when_a_loop_is_running():
    dispatcher = _Dispatcher()

    async def listener(event):
        await asyncio.sleep(0)
        Seen.items.append(event.order_id)
        return "done"

    dispatcher.listen(OrderPlaced, listener)
    results = dispatcher.dispatch_sync(OrderPlaced(8))
    assert len(results) == 1 and isinstance(results[0], asyncio.Task)
    assert await results[0] == "done"
    assert Seen.items == [8]


async def test_scheduled_listener_runs_without_being_awaited():
    dispatcher = _Dispatcher()

    async def listener(event):
        Seen.items.append(event.order_id)

    dispatcher.listen(OrderPlaced, listener)
    dispatcher.dispatch_sync(OrderPlaced(9))  # result dropped, as the event() helper's callers do
    for _ in range(5):
        await asyncio.sleep(0)
    assert Seen.items == [9]


def test_event_helper_runs_async_listeners():
    from hunt.events.dispatcher import Dispatcher
    from hunt.support.helpers import event

    async def listener(evt):
        Seen.items.append(("helper", evt.order_id))

    Dispatcher.listen(OrderPlaced, listener)
    try:
        event(OrderPlaced(3))
    finally:
        Dispatcher.forget(OrderPlaced)
    assert Seen.items == [("helper", 3)]


def test_sync_and_async_listeners_mix_in_registration_order():
    dispatcher = _Dispatcher()

    async def slow(event):
        await asyncio.sleep(0)
        return "async"

    dispatcher.listen(OrderPlaced, lambda e: "sync-1")
    dispatcher.listen(OrderPlaced, slow)
    dispatcher.listen(OrderPlaced, lambda e: "sync-2")
    assert dispatcher.dispatch_sync(OrderPlaced()) == ["sync-1", "async", "sync-2"]
    assert asyncio.run(dispatcher.dispatch(OrderPlaced())) == ["sync-1", "async", "sync-2"]


# ---------------------------------------------------------------------------
# Keys: fully-qualified names, MRO, strings
# ---------------------------------------------------------------------------


def test_same_class_name_in_different_modules_does_not_collide():
    billing = type("Created", (Event,), {"__module__": "app.events.billing"})
    shipping = type("Created", (Event,), {"__module__": "app.events.shipping"})
    dispatcher = _Dispatcher()
    dispatcher.listen(billing, lambda e: Seen.items.append("billing"))
    dispatcher.listen(shipping, lambda e: Seen.items.append("shipping"))

    dispatcher.dispatch_sync(billing())
    assert Seen.items == ["billing"]
    dispatcher.dispatch_sync(shipping())
    assert Seen.items == ["billing", "shipping"]

    dispatcher.forget(billing)
    assert not dispatcher.has_listeners(billing)
    assert dispatcher.has_listeners(shipping)


def test_listener_on_a_base_event_receives_subclass_events():
    dispatcher = _Dispatcher()
    dispatcher.listen(OrderPlaced, lambda e: Seen.items.append(("base", type(e).__name__)))
    dispatcher.listen(RushOrderPlaced, lambda e: Seen.items.append(("rush", type(e).__name__)))

    dispatcher.dispatch_sync(RushOrderPlaced())
    assert Seen.items == [("rush", "RushOrderPlaced"), ("base", "RushOrderPlaced")]
    assert dispatcher.has_listeners(RushOrderPlaced)

    Seen.items = []
    dispatcher.dispatch_sync(OrderPlaced())
    assert Seen.items == [("base", "OrderPlaced")]
    dispatcher.dispatch_sync(Unrelated())
    assert Seen.items == [("base", "OrderPlaced")]


def test_string_keyed_registration_still_works():
    dispatcher = _Dispatcher()
    dispatcher.listen("order.placed", lambda payload: Seen.items.append(("string", payload)))
    dispatcher.listen("OrderPlaced", lambda e: Seen.items.append(("by-name", e.order_id)))

    assert dispatcher.has_listeners("order.placed")
    dispatcher.dispatch_sync("order.placed", {"id": 1})
    assert Seen.items == [("string", {"id": 1})]

    # A listener registered under the bare class name keeps receiving instances.
    dispatcher.dispatch_sync(OrderPlaced(4))
    assert Seen.items[-1] == ("by-name", 4)
    asyncio.run(dispatcher.dispatch(OrderPlaced(5)))
    assert Seen.items[-1] == ("by-name", 5)

    dispatcher.forget("order.placed")
    assert not dispatcher.has_listeners("order.placed")


def test_a_raising_listener_does_not_stop_the_others():
    dispatcher = _Dispatcher()

    def broken(event):
        raise RuntimeError("listener broke")

    dispatcher.listen(OrderPlaced, lambda e: Seen.items.append("before"))
    dispatcher.listen(OrderPlaced, broken)
    dispatcher.listen(OrderPlaced, lambda e: Seen.items.append("after"))

    with pytest.raises(RuntimeError, match="listener broke"):
        dispatcher.dispatch_sync(OrderPlaced())
    assert Seen.items == ["before", "after"]

    Seen.items = []
    with pytest.raises(RuntimeError, match="listener broke"):
        asyncio.run(dispatcher.dispatch(OrderPlaced()))
    assert Seen.items == ["before", "after"]


# ---------------------------------------------------------------------------
# Queued listeners
# ---------------------------------------------------------------------------


class QueuedOrderListener(Job):
    def handle(self, event) -> None:
        Seen.items.append(("queued", type(event).__name__, getattr(event, "order_id", event)))


class QueuedAsyncListener(Job):
    async def handle(self, event) -> None:
        await asyncio.sleep(0)
        Seen.items.append(("queued-async", event.order_id))


class QueuedStringListener(Job):
    def handle(self, payload) -> None:
        Seen.items.append(("queued-string", payload))


@pytest.fixture
def events(monkeypatch):
    """A clean global dispatcher, clean allow-lists and the sync queue driver."""
    from hunt.events import queued
    from hunt.events.dispatcher import Dispatcher
    from hunt.queue.drivers.sync import SyncDriver
    from hunt.queue.manager import Queue

    monkeypatch.setattr(Dispatcher, "_listeners", {})
    monkeypatch.setattr(queued, "_ALLOWED_LISTENER_CLASSES", set())
    monkeypatch.setattr(queued, "_ALLOWED_EVENT_CLASSES", set())
    monkeypatch.setattr(Queue, "_driver", SyncDriver())
    return Dispatcher


def _boot(listen: dict) -> None:
    from unittest.mock import MagicMock

    from hunt.events.provider import EventServiceProvider

    provider = type("Provider", (EventServiceProvider,), {"listen": listen})
    provider(MagicMock()).boot()


def test_queued_listener_round_trips_through_the_queue_payload(events):
    _boot({OrderPlaced: [QueuedOrderListener]})
    events.dispatch_sync(OrderPlaced(11))
    assert Seen.items == [("queued", "OrderPlaced", 11)]


def test_queued_async_listener_is_awaited(events):
    _boot({OrderPlaced: [QueuedAsyncListener]})
    events.dispatch_sync(OrderPlaced(12))
    assert Seen.items == [("queued-async", 12)]


def test_string_event_with_queued_listener_boots_and_dispatches(events):
    """Used to raise AttributeError at boot ('str' has no __module__)."""
    _boot({"order.shipped": [QueuedStringListener]})
    events.dispatch_sync("order.shipped", {"order_id": 5})
    assert Seen.items == [("queued-string", {"order_id": 5})]


def test_allow_list_comes_from_the_listen_map_only(events):
    from hunt.events import queued

    _boot({OrderPlaced: [QueuedOrderListener]})
    before = set(queued._ALLOWED_EVENT_CLASSES)
    assert before == {f"{__name__}.OrderPlaced"}
    assert queued._ALLOWED_LISTENER_CLASSES == {f"{__name__}.QueuedOrderListener"}

    # Dispatching a subclass reaches the base listener and runs on the worker,
    # without the dispatching process widening the allow-list.
    events.dispatch_sync(RushOrderPlaced(13))
    assert Seen.items == [("queued", "RushOrderPlaced", 13)]
    assert queued._ALLOWED_EVENT_CLASSES == before


def test_worker_refuses_event_classes_outside_the_listen_map(events):
    from hunt.events.queued import QueuedEventListener

    _boot({OrderPlaced: [QueuedOrderListener]})
    listener = f"{__name__}.QueuedOrderListener"

    with pytest.raises(ValueError, match="unregistered event"):
        QueuedEventListener(listener, f"{__name__}.Unrelated", {"order_id": 1}).handle()
    with pytest.raises(ValueError, match="unregistered event"):
        QueuedEventListener(listener, "subprocess.Popen", {}).handle()
    with pytest.raises(ValueError, match="unregistered event"):
        # A module that is not loaded is never imported to find out.
        QueuedEventListener(listener, "hunt_not_a_real_module.Thing", {}).handle()
    with pytest.raises(ValueError, match="unregistered listener"):
        QueuedEventListener(f"{__name__}.QueuedAsyncListener", f"{__name__}.OrderPlaced", {}).handle()
    assert Seen.items == []


def test_event_carrying_unserializable_data_fails_at_dispatch_with_the_attribute_named(events):
    from hunt.queue.job import JobSerializationError

    _boot({OrderPlaced: [QueuedOrderListener]})
    event = OrderPlaced(1)
    event.connection = object()
    with pytest.raises(JobSerializationError, match=r"event_data\['connection'\]"):
        events.dispatch_sync(event)
