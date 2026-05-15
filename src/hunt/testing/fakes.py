from __future__ import annotations

import datetime as _dt_mod
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# EventFake
# ---------------------------------------------------------------------------


class EventFake:
    """Replace the event Dispatcher with a recording fake.

    Usage::

        with EventFake() as fake:
            dispatcher.dispatch(UserRegistered(user))
            fake.assert_dispatched(UserRegistered)
    """

    def __init__(self) -> None:
        self._dispatched: list[Any] = []

    def __enter__(self) -> EventFake:
        from hunt.events.dispatcher import Dispatcher

        fake = self

        async def _fake_dispatch(event: Any, payload: Any = None) -> list:
            item = payload if payload is not None else event
            fake._dispatched.append(item)
            return []

        def _fake_dispatch_sync(event: Any, payload: Any = None) -> list:
            item = payload if payload is not None else event
            fake._dispatched.append(item)
            return []

        self._dp_dispatch = patch.object(Dispatcher, "dispatch", _fake_dispatch)
        self._dp_sync = patch.object(Dispatcher, "dispatch_sync", _fake_dispatch_sync)
        self._dp_dispatch.start()
        self._dp_sync.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._dp_dispatch.stop()
        self._dp_sync.stop()

    # Assertions
    def assert_dispatched(self, cls: type, count: int | None = None) -> None:
        found = [e for e in self._dispatched if isinstance(e, cls)]
        if count is not None:
            assert len(found) == count, f"Expected {count} dispatch(es) of {cls.__name__}, got {len(found)}"
        else:
            assert found, f"Expected {cls.__name__} to be dispatched, but it was not"

    def assert_not_dispatched(self, cls: type) -> None:
        found = [e for e in self._dispatched if isinstance(e, cls)]
        assert not found, f"Expected {cls.__name__} NOT to be dispatched, but it was"

    def assert_nothing_dispatched(self) -> None:
        assert not self._dispatched, f"Expected no events dispatched, got {len(self._dispatched)}"

    def dispatched(self, cls: type) -> list[Any]:
        return [e for e in self._dispatched if isinstance(e, cls)]


# ---------------------------------------------------------------------------
# QueueFake
# ---------------------------------------------------------------------------


class QueueFake:
    """Replace the Queue driver with a recording fake.

    Usage::

        with QueueFake() as fake:
            ProcessOrder(order).dispatch()
            fake.assert_pushed(ProcessOrder)
    """

    def __init__(self) -> None:
        self._jobs: list[Any] = []
        self._delays: dict[int, int] = {}  # id(job) → delay seconds

    def __enter__(self) -> QueueFake:
        fake = self

        class _FakeDriver:
            def push(self, job: Any) -> None:
                fake._jobs.append(job)

            def later(self, delay: int, job: Any) -> None:
                fake._jobs.append(job)
                fake._delays[id(job)] = delay

            def push_payload(self, body_dict: Any, queue: str = "default") -> None:
                fake._jobs.append(body_dict)

            def pop(self, queue: str = "default") -> None:
                return None

            def delete(self, job_id: Any) -> None:
                pass

            def release(self, job_id: Any, delay: int = 0) -> None:
                pass

            def fail(self, *args: Any) -> None:
                pass

            def size(self, queue: str = "default") -> int:
                return 0

        from hunt.queue.manager import Queue

        self._patcher = patch.object(Queue, "_get_driver", return_value=_FakeDriver())
        self._patcher.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._patcher.stop()

    # Assertions
    def assert_pushed(self, cls: type, count: int | None = None, callback: Any = None) -> None:
        found = [j for j in self._jobs if isinstance(j, cls)]
        if callback is not None:
            found = [j for j in found if callback(j)]
        if count is not None:
            assert len(found) == count, f"Expected {count} push(es) of {cls.__name__}, got {len(found)}"
        else:
            assert found, f"Expected {cls.__name__} to be pushed, but it was not"

    def assert_not_pushed(self, cls: type) -> None:
        found = [j for j in self._jobs if isinstance(j, cls)]
        assert not found, f"Expected {cls.__name__} NOT to be pushed, but it was"

    def assert_nothing_pushed(self) -> None:
        assert not self._jobs, f"Expected no jobs pushed, got {len(self._jobs)}"

    def pushed(self, cls: type) -> list[Any]:
        return [j for j in self._jobs if isinstance(j, cls)]

    def delay_for(self, job: Any) -> int | None:
        return self._delays.get(id(job))


# ---------------------------------------------------------------------------
# freeze_time
# ---------------------------------------------------------------------------


@contextmanager
def freeze_time(frozen_dt: _dt_mod.datetime | None = None):
    """Freeze time.time() and datetime.datetime.now() to a fixed instant.

    Usage::

        with freeze_time(datetime(2026, 1, 1, 12, 0, 0)) as frozen:
            assert time.time() == frozen.timestamp()
    """
    if frozen_dt is None:
        frozen_dt = _dt_mod.datetime.now()

    frozen_ts = frozen_dt.timestamp()

    class _FakeDatetime(_dt_mod.datetime):
        @classmethod
        def now(cls, tz: Any = None) -> _FakeDatetime:
            if tz is not None:
                return frozen_dt.replace(tzinfo=tz)  # type: ignore[return-value]
            return frozen_dt  # type: ignore[return-value]

        @classmethod
        def utcnow(cls) -> _FakeDatetime:
            return frozen_dt  # type: ignore[return-value]

    with (
        patch("time.time", return_value=frozen_ts),
        patch("time.monotonic", return_value=frozen_ts),
        patch("datetime.datetime", _FakeDatetime),
    ):
        yield frozen_dt
