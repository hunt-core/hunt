from __future__ import annotations

import inspect
from typing import Any, Callable, Type


class Event:
    """Base class for application events."""


class _Dispatcher:
    """Synchronous event dispatcher with async listener support."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}

    def listen(self, event: str | Type[Event], listener: Callable) -> None:
        key = event if isinstance(event, str) else event.__name__
        self._listeners.setdefault(key, []).append(listener)

    async def dispatch(self, event: Event | str, payload: Any = None) -> list[Any]:
        if isinstance(event, str):
            key = event
        else:
            key = type(event).__name__
            payload = event

        results = []
        for listener in self._listeners.get(key, []):
            result = listener(payload)
            if inspect.iscoroutine(result):
                result = await result
            results.append(result)
        return results

    def dispatch_sync(self, event: Event | str, payload: Any = None) -> list[Any]:
        if isinstance(event, str):
            key = event
        else:
            key = type(event).__name__
            payload = event

        results = []
        for listener in self._listeners.get(key, []):
            result = listener(payload)
            results.append(result)
        return results

    def forget(self, event: str | Type[Event]) -> None:
        key = event if isinstance(event, str) else event.__name__
        self._listeners.pop(key, None)

    def has_listeners(self, event: str | Type[Event]) -> bool:
        key = event if isinstance(event, str) else event.__name__
        return bool(self._listeners.get(key))


Dispatcher = _Dispatcher()
