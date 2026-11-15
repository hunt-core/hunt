from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from hunt.container.provider import ServiceProvider
from hunt.events.dispatcher import Dispatcher

if TYPE_CHECKING:
    pass


def _should_queue(cls: type) -> bool:
    """Return True if the listener class should be dispatched via the queue."""
    if getattr(cls, "implements_queued_listener", False):
        return True
    try:
        from hunt.queue.job import Job

        return isinstance(cls, type) and issubclass(cls, Job)
    except Exception:
        return False


def _make_queued_wrapper(listener_cls: type, event_cls: type | str):
    """Return a callable that pushes a QueuedEventListener to the queue.

    The worker's allow-list is built here, at boot, from the ``listen`` map
    only: the listener class and (for class events) the event class. Nothing
    is added at dispatch time, because the worker process would never see it.
    """
    from hunt.events.queued import QueuedEventListener, allow_event, allow_listener
    from hunt.queue.manager import Queue

    listener_path = f"{listener_cls.__module__}.{listener_cls.__name__}"
    allow_listener(listener_path)
    if not isinstance(event_cls, str):
        allow_event(f"{event_cls.__module__}.{event_cls.__name__}")

    def _wrapper(event) -> None:
        if isinstance(event, (str, int, float, bool, list, tuple, dict)) or event is None:
            # String-keyed events carry an arbitrary payload, not an event object.
            job = QueuedEventListener(listener_class=listener_path, event_class="", event_data={"payload": event})
        else:
            job = QueuedEventListener(
                listener_class=listener_path,
                event_class=f"{type(event).__module__}.{type(event).__name__}",
                event_data={k: v for k, v in vars(event).items() if not k.startswith("_")},
            )
        Queue.push(job)

    return _wrapper


class EventServiceProvider(ServiceProvider):
    """Declare event-to-listener mappings and subscriber classes.

    The framework registers all listeners and subscribers with the
    Dispatcher during ``boot()``.

    **Inline listeners** (called synchronously)::

        class AppEventServiceProvider(EventServiceProvider):
            listen = {
                UserRegistered: [SendWelcomeEmail, LogNewUser],
            }

    **Queued listeners** — either inherit from ``Job`` or set the flag::

        class SendWelcomeEmail(Job):
            implements_queued_listener = True  # optional, inferred from Job subclass

            def handle(self, event: UserRegistered) -> None: ...

        class AppEventServiceProvider(EventServiceProvider):
            listen = {UserRegistered: [SendWelcomeEmail]}

    **Event subscribers** — a single class that subscribes to multiple events::

        class UserEventSubscriber:
            def subscribe(self, dispatcher) -> None:
                dispatcher.listen(UserRegistered, self.on_registered)
                dispatcher.listen(UserLoggedIn, self.on_login)

            def on_registered(self, event): ...
            def on_login(self, event): ...

        class AppEventServiceProvider(EventServiceProvider):
            subscribe = [UserEventSubscriber]
    """

    listen: ClassVar[dict] = {}
    subscribe: ClassVar[list] = []

    def boot(self) -> None:
        # Register listeners from the listen dict
        for event, listeners in self.listen.items():
            for listener_cls in listeners:
                if _should_queue(listener_cls):
                    Dispatcher.listen(event, _make_queued_wrapper(listener_cls, event))
                else:
                    instance = listener_cls()
                    Dispatcher.listen(event, instance.handle)

        # Register event subscribers
        for subscriber_cls in self.subscribe:
            instance = subscriber_cls()
            instance.subscribe(Dispatcher)
