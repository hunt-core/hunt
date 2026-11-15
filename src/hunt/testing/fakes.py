from __future__ import annotations

import datetime as _dt_mod
import sys
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
# NotificationFake
# ---------------------------------------------------------------------------


class NotificationFake:
    """Replace the notification sender with a recording fake.

    Usage::

        with NotificationFake() as fake:
            user.notify(InvoiceReady(invoice))
            fake.assert_sent_to(user, InvoiceReady)
    """

    def __init__(self) -> None:
        self._sent: list[tuple[Any, Any]] = []  # [(notifiable, notification), ...]

    def __enter__(self) -> NotificationFake:
        fake = self

        class _FakeSender:
            def __init__(self, notification: Any, notifiable: Any) -> None:
                self._notification = notification
                self._notifiable = notifiable

            def send(self) -> None:
                fake._sent.append((self._notifiable, self._notification))

            # notify_now() must be recorded too, not delivered for real.
            send_now = send

        from hunt.notifications import notifiable as _notifiable_mod

        self._patcher = patch.object(_notifiable_mod, "_NotificationSender", _FakeSender)
        self._patcher.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._patcher.stop()

    def assert_sent_to(self, notifiable: Any, cls: type, count: int | None = None) -> None:
        found = [n for nb, n in self._sent if nb is notifiable and isinstance(n, cls)]
        if count is not None:
            assert len(found) == count, f"Expected {count} {cls.__name__} to {notifiable!r}, got {len(found)}"
        else:
            assert found, f"Expected {cls.__name__} to be sent to {notifiable!r}, but it was not"

    def assert_not_sent_to(self, notifiable: Any, cls: type) -> None:
        found = [n for nb, n in self._sent if nb is notifiable and isinstance(n, cls)]
        assert not found, f"Expected {cls.__name__} NOT to be sent to {notifiable!r}"

    def assert_nothing_sent(self) -> None:
        assert not self._sent, f"Expected no notifications sent, got {len(self._sent)}"

    def sent(self, cls: type) -> list[Any]:
        return [n for _, n in self._sent if isinstance(n, cls)]


# ---------------------------------------------------------------------------
# MailFake
# ---------------------------------------------------------------------------


class MailFake:
    """Replace the mail transport with a recording fake.

    Built on ``Mail.fake()`` / ``Mail.stop_faking()``: while the block is
    active every ``Mail.send()``, ``Mail.to(...).send()``, ``queue()`` and
    ``later()`` is recorded and nothing reaches the configured transport or
    the queue.  The previous state is restored on exit.

    Usage::

        with MailFake() as fake:
            Mail.to("user@example.com").send(WelcomeMail(user))
            fake.assert_sent(WelcomeMail)
            fake.assert_sent_to("user@example.com", WelcomeMail)
    """

    def __init__(self) -> None:
        from hunt.mail.manager import _MailFake

        # Replaced by the installed fake on __enter__; kept so assertions made
        # after the block still see what was recorded.
        self._fake: Any = _MailFake()
        self._previous: Any = None

    def __enter__(self) -> MailFake:
        from hunt.mail.manager import Mail

        self._previous = Mail._fake
        self._fake = Mail.fake()
        return self

    def __exit__(self, *args: Any) -> None:
        from hunt.mail.manager import Mail

        Mail.stop_faking()
        # Restore an outer fake (nested use) instead of the real transport.
        Mail._fake = self._previous
        self._previous = None

    def assert_sent(self, cls: type, count: int | None = None, callback: Any = None) -> None:
        self._fake.assert_sent(cls, count, callback)

    def assert_not_sent(self, cls: type) -> None:
        self._fake.assert_not_sent(cls)

    def assert_sent_to(self, to: str, cls: type | None = None) -> None:
        self._fake.assert_sent_to(to, cls)

    def assert_queued(self, cls: type, count: int | None = None) -> None:
        self._fake.assert_queued(cls, count)

    def assert_nothing_sent(self) -> None:
        self._fake.assert_nothing_sent()

    def sent(self, cls: type | None = None) -> list[Any]:
        return self._fake.sent(cls)

    def queued(self, cls: type | None = None) -> list[Any]:
        return self._fake.queued(cls)


# ---------------------------------------------------------------------------
# freeze_time
# ---------------------------------------------------------------------------

_real_datetime = _dt_mod.datetime
_real_date = _dt_mod.date

# Unix timestamps of the active freezes (innermost last).
_frozen_stack: list[float] = []


class _FrozenDateMeta(type):
    """Make ``isinstance`` / ``issubclass`` against the patched ``datetime.date`` behave like the real class."""

    def __instancecheck__(cls, obj: Any) -> bool:
        return isinstance(obj, _real_date)

    def __subclasscheck__(cls, sub: type) -> bool:
        return issubclass(sub, _real_date)


class _FrozenDatetimeMeta(type):
    """Make ``isinstance`` / ``issubclass`` against the patched ``datetime.datetime`` behave like the real class."""

    def __instancecheck__(cls, obj: Any) -> bool:
        return isinstance(obj, _real_datetime)

    def __subclasscheck__(cls, sub: type) -> bool:
        return issubclass(sub, _real_datetime)


class _FrozenDate(_real_date, metaclass=_FrozenDateMeta):
    """``datetime.date`` stand-in whose ``today()`` is the frozen day.

    It never produces instances of itself: constructing it, and every
    classmethod, returns a plain ``datetime.date``.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _real_date(*args, **kwargs)

    @classmethod
    def today(cls) -> Any:
        if not _frozen_stack:
            return _real_date.today()
        return _real_datetime.fromtimestamp(_frozen_stack[-1]).date()


class _FrozenDatetime(_real_datetime, metaclass=_FrozenDatetimeMeta):
    """``datetime.datetime`` stand-in whose "now" is the frozen instant.

    It never produces instances of itself: constructing it, ``now()`` and the
    other classmethods all return plain ``datetime.datetime`` objects, so the
    values behave exactly like ordinary datetimes (database drivers,
    serializers, ``type(x) is datetime``).  ``isinstance(x, datetime.datetime)``
    keeps working while the class is patched in because of the metaclass.
    Outside a ``freeze_time`` block it behaves exactly like ``datetime``.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _real_datetime(*args, **kwargs)

    @classmethod
    def now(cls, tz: Any = None) -> Any:
        if not _frozen_stack:
            return _real_datetime.now(tz)
        # fromtimestamp converts the frozen instant into tz (or local time).
        return _real_datetime.fromtimestamp(_frozen_stack[-1], tz)

    @classmethod
    def today(cls) -> Any:
        return cls.now()

    @classmethod
    def utcnow(cls) -> Any:
        if not _frozen_stack:
            return _real_datetime.now(_dt_mod.UTC).replace(tzinfo=None)
        return _real_datetime.fromtimestamp(_frozen_stack[-1], _dt_mod.UTC).replace(tzinfo=None)


def _modules_to_patch(prefixes: tuple[str, ...]) -> list[Any]:
    found = []
    for name, module in list(sys.modules.items()):
        if module is None or module is _dt_mod or name == __name__:
            continue
        if any(name == p or name.startswith(p + ".") for p in prefixes):
            found.append(module)
    return found


@contextmanager
def freeze_time(frozen_dt: _dt_mod.datetime | None = None, modules: tuple[str, ...] | list[str] = ()):
    """Freeze the clock at a fixed instant.

    Usage::

        with freeze_time(datetime(2026, 1, 1, 12, 0, 0)) as frozen:
            assert time.time() == frozen.timestamp()
            assert datetime.datetime.now() == frozen

    *frozen_dt* may be naive (interpreted as local time, like
    ``datetime.timestamp()``) or timezone-aware; it defaults to the current
    time.  The block yields *frozen_dt*.

    What is frozen:

    * ``time.time()``, ``time.time_ns()`` and ``time.monotonic()``;
    * ``datetime.datetime.now()`` / ``today()`` / ``utcnow()`` and
      ``datetime.date.today()`` reached through the ``datetime`` module.
      ``now(tz)`` converts the frozen instant into ``tz``.  The values are
      plain ``datetime`` / ``date`` instances, and ``isinstance(x,
      datetime.datetime)`` works for them and for any other datetime created
      before or during the block;
    * the names ``datetime`` / ``date`` (under any alias) imported with
      ``from datetime import datetime`` into modules that are already loaded
      and belong to the ``hunt`` package (e.g. ``hunt.scheduling``) or to a
      package listed in *modules* (pass your app's package:
      ``freeze_time(dt, modules=["app"])``).

    Limits:

    * a module outside ``hunt`` / *modules* that did ``from datetime import
      datetime`` (or ``from time import time``) keeps the real clock;
    * C extensions and anything calling the OS clock directly (database
      ``CURRENT_TIMESTAMP``, ``asyncio`` loop time, ``time.sleep``,
      ``time.perf_counter``) are not affected;
    * the patch is process-global and not thread-safe: do not run frozen
      blocks concurrently in several threads.
    """
    if frozen_dt is None:
        frozen_dt = _real_datetime.now()

    frozen_ts = frozen_dt.timestamp()

    patches: list[Any] = [
        patch("time.time", return_value=frozen_ts),
        patch("time.time_ns", return_value=int(frozen_ts * 1_000_000_000)),
        patch("time.monotonic", return_value=frozen_ts),
        patch("datetime.datetime", _FrozenDatetime),
        patch("datetime.date", _FrozenDate),
    ]
    # Rebind names captured by "from datetime import datetime/date" in
    # framework (and requested) modules.
    for module in _modules_to_patch(("hunt", *modules)):
        for attr, value in list(vars(module).items()):
            if value is _real_datetime:
                patches.append(patch.object(module, attr, _FrozenDatetime))
            elif value is _real_date:
                patches.append(patch.object(module, attr, _FrozenDate))

    _frozen_stack.append(frozen_ts)
    started: list[Any] = []
    try:
        for p in patches:
            p.start()
            started.append(p)
        yield frozen_dt
    finally:
        for p in reversed(started):
            p.stop()
        _frozen_stack.pop()
