from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from hunt.notifications.notification import Notification


class NotificationFake:
    """Intercepts notification dispatching for test assertions.

    Usage::

        with NotificationFake() as fake:
            user.notify(InvoiceReady(invoice))
            fake.assert_sent_to(user, InvoiceReady)
    """

    def __init__(self) -> None:
        self._sent: list[tuple[Any, Notification]] = []
        self._patcher: Any = None

    def __enter__(self) -> NotificationFake:
        from hunt.notifications.notifiable import _NotificationSender

        fake = self

        def _patched_send(sender_self: Any) -> None:
            fake._sent.append((sender_self._notifiable, sender_self._notification))

        self._patcher = patch.object(_NotificationSender, "send", _patched_send)
        self._patcher.start()
        return self

    def __exit__(self, *args: Any) -> None:
        if self._patcher:
            self._patcher.stop()

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def sent_to(
        self,
        notifiable: Any,
        notification_class: type | None = None,
    ) -> list[Notification]:
        results = [n for (recv, n) in self._sent if recv is notifiable]
        if notification_class:
            results = [n for n in results if isinstance(n, notification_class)]
        return results

    def assert_sent_to(
        self,
        notifiable: Any,
        notification_class: type,
        callback: Callable | None = None,
    ) -> None:
        matches = self.sent_to(notifiable, notification_class)
        if callback:
            matches = [n for n in matches if callback(n)]
        assert matches, f"Expected {notification_class.__name__} to be sent to {notifiable!r}, but it was not."

    def assert_not_sent_to(self, notifiable: Any, notification_class: type) -> None:
        matches = self.sent_to(notifiable, notification_class)
        assert not matches, (
            f"Expected {notification_class.__name__} NOT to be sent to "
            f"{notifiable!r}, but it was sent {len(matches)} time(s)."
        )

    def assert_nothing_sent(self) -> None:
        assert not self._sent, f"Expected no notifications to be sent, but {len(self._sent)} were sent."

    def assert_count(self, notification_class: type, count: int) -> None:
        matches = [n for (_, n) in self._sent if isinstance(n, notification_class)]
        assert len(matches) == count, (
            f"Expected {notification_class.__name__} to be sent {count} time(s), "
            f"but it was sent {len(matches)} time(s)."
        )
