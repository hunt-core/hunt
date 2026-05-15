from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from hunt.notifications.notification import Notification


class Notifiable:
    """Mixin for Model subclasses that can receive notifications.

    Usage::

        class User(Model, Notifiable):
            table = "users"
            ...

        user.notify(InvoiceReady(invoice))
    """

    def notify(self, notification: Notification) -> None:
        """Dispatch the notification through its configured channels."""
        _NotificationSender(notification, self).send()

    def notify_now(self, notification: Notification) -> None:
        """Dispatch immediately, bypassing any queue."""
        _NotificationSender(notification, self).send()

    def route_notification_for_mail(self) -> str | None:
        """Return the email address for mail notifications."""
        if hasattr(self, "_attributes"):
            return self._attributes.get("email")  # type: ignore[attr-defined]
        return getattr(self, "email", None)

    def route_notification_for_database(self) -> Any:
        """Return the notifiable key for database notifications."""
        if hasattr(self, "_attributes"):
            return self._attributes.get("id")  # type: ignore[attr-defined]
        return getattr(self, "id", None)

    def notifications(self) -> list[dict]:
        """Return all database notifications for this notifiable."""
        return _fetch_notifications(self, unread_only=False)

    def unread_notifications(self) -> list[dict]:
        """Return unread database notifications."""
        return _fetch_notifications(self, unread_only=True)

    def read_notifications(self) -> list[dict]:
        """Return read database notifications."""
        return _fetch_notifications(self, read_only=True)

    def mark_notifications_read(self) -> None:
        """Mark all unread notifications as read."""
        import time

        from sqlalchemy import text

        from hunt.database.connection import connection

        notifiable_id = self.route_notification_for_database()
        notifiable_type = type(self).__name__
        now = int(time.time())

        with connection().connect() as conn:
            conn.execute(
                text(
                    "UPDATE notifications SET read_at = :t WHERE "
                    "notifiable_id = :nid AND notifiable_type = :ntype AND read_at IS NULL"
                ),
                {"t": now, "nid": notifiable_id, "ntype": notifiable_type},
            )
            conn.commit()


def _fetch_notifications(
    notifiable: Any,
    unread_only: bool = False,
    read_only: bool = False,
) -> list[dict]:
    import json

    from sqlalchemy import text

    from hunt.database.connection import connection

    notifiable_id = notifiable.route_notification_for_database()
    notifiable_type = type(notifiable).__name__

    where = "notifiable_id = :nid AND notifiable_type = :ntype"
    if unread_only:
        where += " AND read_at IS NULL"
    elif read_only:
        where += " AND read_at IS NOT NULL"

    try:
        with connection().connect() as conn:
            result = conn.execute(
                text(f"SELECT * FROM notifications WHERE {where} ORDER BY created_at DESC"),
                {"nid": notifiable_id, "ntype": notifiable_type},
            )
            keys = list(result.keys())
            rows = result.fetchall()
    except Exception:
        return []

    notifications = []
    for row in rows:
        d = dict(zip(keys, row, strict=False))
        if "data" in d and isinstance(d["data"], str):
            try:
                d["data"] = json.loads(d["data"])
            except Exception:
                pass
        notifications.append(d)
    return notifications


class _NotificationSender:
    CHANNELS: ClassVar[dict] = {
        "mail": "hunt.notifications.channels.mail.MailChannel",
        "database": "hunt.notifications.channels.database.DatabaseChannel",
    }

    def __init__(self, notification: Notification, notifiable: Any) -> None:
        self._notification = notification
        self._notifiable = notifiable

    def send(self) -> None:
        channels = self._notification.via(self._notifiable)
        for channel in channels:
            driver = self._resolve(channel)
            if driver:
                driver.send(self._notifiable, self._notification)

    def _resolve(self, channel: str) -> Any:
        dotted = self.CHANNELS.get(channel)
        if not dotted:
            return None
        module_path, cls_name = dotted.rsplit(".", 1)
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, cls_name)()
