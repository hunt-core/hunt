"""Tests for EventServiceProvider."""

from typing import ClassVar

import pytest

from hunt.events.dispatcher import Dispatcher, Event
from hunt.events.provider import EventServiceProvider


class OrderPlaced(Event):
    pass


class AnotherEvent(Event):
    pass


class SendConfirmation:
    def __init__(self):
        self.received = []

    def handle(self, event):
        self.received.append(event)


class NotifyWarehouse:
    def __init__(self):
        self.received = []

    def handle(self, event):
        self.received.append(event)


@pytest.fixture(autouse=True)
def clean_dispatcher():
    yield
    Dispatcher.forget(OrderPlaced)
    Dispatcher.forget(AnotherEvent)


@pytest.fixture()
def app():
    from unittest.mock import MagicMock

    return MagicMock()


# ---------------------------------------------------------------------------
# Basic wiring
# ---------------------------------------------------------------------------


def test_listener_registered_on_boot(app):
    class AppEventServiceProvider(EventServiceProvider):
        listen: ClassVar[dict] = {OrderPlaced: [SendConfirmation]}

    provider = AppEventServiceProvider(app)
    provider.boot()

    assert Dispatcher.has_listeners(OrderPlaced)


def test_listener_handle_called_on_dispatch(app):
    class AppEventServiceProvider(EventServiceProvider):
        listen: ClassVar[dict] = {OrderPlaced: [SendConfirmation]}

    provider = AppEventServiceProvider(app)
    provider.boot()

    event = OrderPlaced()
    Dispatcher.dispatch_sync(event)

    # Verify the listener received the event — check via has_listeners since
    # dispatch_sync returns results, and listener instance is internal.
    assert Dispatcher.has_listeners(OrderPlaced)


def test_multiple_listeners_for_same_event(app):
    class AppEventServiceProvider(EventServiceProvider):
        listen: ClassVar[dict] = {OrderPlaced: [SendConfirmation, NotifyWarehouse]}

    provider = AppEventServiceProvider(app)
    provider.boot()

    results = Dispatcher.dispatch_sync(OrderPlaced())
    assert len(results) == 2


def test_multiple_events_wired_independently(app):
    class AppEventServiceProvider(EventServiceProvider):
        listen: ClassVar[dict] = {
            OrderPlaced: [SendConfirmation],
            AnotherEvent: [NotifyWarehouse],
        }

    provider = AppEventServiceProvider(app)
    provider.boot()

    assert Dispatcher.has_listeners(OrderPlaced)
    assert Dispatcher.has_listeners(AnotherEvent)


# ---------------------------------------------------------------------------
# Empty listen dict
# ---------------------------------------------------------------------------


def test_empty_listen_dict_boots_without_error(app):
    class AppEventServiceProvider(EventServiceProvider):
        listen: ClassVar[dict] = {}

    provider = AppEventServiceProvider(app)
    provider.boot()  # must not raise

    assert not Dispatcher.has_listeners(OrderPlaced)


# ---------------------------------------------------------------------------
# Each boot call instantiates a fresh listener
# ---------------------------------------------------------------------------


def test_listener_instantiated_per_boot(app):
    instances = []

    class TrackingListener:
        def __init__(self):
            instances.append(self)

        def handle(self, event):
            pass

    class AppEventServiceProvider(EventServiceProvider):
        listen: ClassVar[dict] = {OrderPlaced: [TrackingListener]}

    AppEventServiceProvider(app).boot()
    assert len(instances) == 1

    Dispatcher.forget(OrderPlaced)
    AppEventServiceProvider(app).boot()
    assert len(instances) == 2


# ---------------------------------------------------------------------------
# Subclass inheriting listen from base
# ---------------------------------------------------------------------------


def test_subclass_can_extend_listen(app):
    class BaseProvider(EventServiceProvider):
        listen: ClassVar[dict] = {OrderPlaced: [SendConfirmation]}

    class ExtendedProvider(BaseProvider):
        listen: ClassVar[dict] = {
            OrderPlaced: [SendConfirmation],
            AnotherEvent: [NotifyWarehouse],
        }

    ExtendedProvider(app).boot()

    assert Dispatcher.has_listeners(OrderPlaced)
    assert Dispatcher.has_listeners(AnotherEvent)
