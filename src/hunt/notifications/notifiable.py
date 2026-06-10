from __future__ import annotations

import base64
import pickle
from typing import TYPE_CHECKING, Any, ClassVar

from hunt.queue.job import Job

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

    def route_notification_for_slack(self) -> str | None:
        """Return the Slack webhook URL for this notifiable. Override to customise."""
        return None

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

    def mark_notification_read(self, notification_id: str) -> None:
        """Mark a single notification as read by its ID."""
        import time

        from sqlalchemy import text

        from hunt.database.connection import connection

        now = int(time.time())
        with connection().connect() as conn:
            conn.execute(
                text("UPDATE notifications SET read_at = :t WHERE id = :id AND read_at IS NULL"),
                {"t": now, "id": notification_id},
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
        "slack": "hunt.notifications.channels.slack.SlackChannel",
    }

    def __init__(self, notification: Notification, notifiable: Any) -> None:
        self._notification = notification
        self._notifiable = notifiable

    def send(self) -> None:
        should_queue = getattr(self._notification, "should_queue", False)
        channels = self._notification.via(self._notifiable)
        for channel in channels:
            if should_queue:
                self._queue_channel(channel)
            else:
                driver = self._resolve(channel)
                if driver:
                    driver.send(self._notifiable, self._notification)

    def _queue_channel(self, channel: str) -> None:
        try:
            from hunt.queue.manager import Queue

            Queue.push(_SendNotificationJob(self._notification, self._notifiable, channel))
        except Exception as exc:
            try:
                from hunt.log.manager import Log

                Log.warning(f"Queueing notification failed — sending synchronously instead: {exc}")
            except Exception:
                pass
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


class _SendNotificationJob(Job):
    """Delivers a single notification channel via the queue.

    Notification and notifiable are pickled into a public ``payload``
    attribute so the job survives the JSON round-trip to a real queue backend
    and can be rebuilt on the worker. The pickle is only ever loaded from
    inside the HMAC-signed job envelope, which the worker verifies before
    deserialising anything.
    """

    queue: str = "default"
    tries: int = 3

    def __init__(
        self,
        notification: Any = None,
        notifiable: Any = None,
        channel: str = "",
        payload: str = "",
    ) -> None:
        if notification is not None:
            payload = base64.b64encode(pickle.dumps((notification, notifiable))).decode()
        self.payload = payload
        self.channel = channel

    def handle(self) -> None:
        notification, notifiable = pickle.loads(base64.b64decode(self.payload))
        driver = _NotificationSender(notification, notifiable)._resolve(self.channel)
        if driver:
            driver.send(notifiable, notification)

    def failed(self, exc: Exception) -> None:
        pass
