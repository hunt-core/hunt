from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from hunt.queue._logging import log_error


class Event:
    """Base class for application events."""


def event_key(event: str | type) -> str:
    """Registry key: the string itself, or a class's fully-qualified name."""
    if isinstance(event, str):
        return event
    return f"{event.__module__}.{event.__qualname__}"


# Tasks created for async listeners dispatched synchronously inside a running
# loop. Held here so they are not garbage-collected before they finish.
_background_tasks: set[asyncio.Task] = set()


def _task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        log_error(f"Async event listener failed: {task.exception()!r}")


class _Dispatcher:
    """Event dispatcher supporting sync and async listeners.

    Listeners registered for a class are keyed by its fully-qualified name, so
    two event classes that share a name in different modules do not collide,
    and they also receive events that are instances of subclasses. Listeners
    registered under a plain string are called for that string and, as before,
    for event objects whose class has that bare name.
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}

    def listen(self, event: str | type[Event], listener: Callable) -> None:
        self._listeners.setdefault(event_key(event), []).append(listener)

    def _listeners_for_class(self, cls: type) -> list[Callable]:
        found: list[Callable] = []
        for klass in cls.__mro__:
            if klass is object:
                continue
            found.extend(self._listeners.get(event_key(klass), []))
            # String-keyed registration by bare class name.
            found.extend(self._listeners.get(klass.__name__, []))
        return found

    def _resolve(self, event: Event | str, payload: Any) -> tuple[list[Callable], Any]:
        if isinstance(event, str):
            return list(self._listeners.get(event, [])), payload
        return self._listeners_for_class(type(event)), event

    async def dispatch(self, event: Event | str, payload: Any = None) -> list[Any]:
        listeners, payload = self._resolve(event, payload)
        results = []
        first_error: Exception | None = None
        for listener in listeners:
            try:
                result = listener(payload)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                # One failing listener must not stop the others from running.
                if first_error is None:
                    first_error = exc
                else:
                    log_error(f"Event listener {listener!r} failed: {exc!r}")
                continue
            results.append(result)
        if first_error is not None:
            raise first_error
        return results

    def dispatch_sync(self, event: Event | str, payload: Any = None) -> list[Any]:
        """Dispatch from synchronous code.

        Async listeners are run too: to completion when no event loop is
        running in this thread, otherwise they are scheduled on the running
        loop and their ``asyncio.Task`` is returned in place of a result.
        """
        listeners, payload = self._resolve(event, payload)
        results = []
        first_error: Exception | None = None
        for listener in listeners:
            try:
                result = listener(payload)
                if inspect.isawaitable(result):
                    result = self._run_async(result)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                else:
                    log_error(f"Event listener {listener!r} failed: {exc!r}")
                continue
            results.append(result)
        if first_error is not None:
            raise first_error
        return results

    @staticmethod
    def _run_async(awaitable: Any) -> Any:
        async def _await() -> Any:
            return await awaitable

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_await())
        task = loop.create_task(_await())
        _background_tasks.add(task)
        task.add_done_callback(_task_done)
        return task

    def forget(self, event: str | type[Event]) -> None:
        self._listeners.pop(event_key(event), None)

    def has_listeners(self, event: str | type[Event]) -> bool:
        if isinstance(event, str):
            return bool(self._listeners.get(event))
        return bool(self._listeners_for_class(event))


Dispatcher = _Dispatcher()
