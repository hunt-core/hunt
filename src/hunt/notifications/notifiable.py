from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from hunt.notifications.notification import type_names
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
        """Dispatch the notification through its configured channels.

        Queued when the notification sets ``should_queue = True``.
        """
        _NotificationSender(notification, self).send()

    def notify_now(self, notification: Notification) -> None:
        """Dispatch immediately, bypassing the queue even if ``should_queue`` is set."""
        _NotificationSender(notification, self).send_now()

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

        from hunt.database.connection import connection, use_connection

        notifiable_id = self.route_notification_for_database()
        notifiable_type, legacy_type = type_names(type(self))
        now = int(time.time())

        with use_connection(connection()) as conn:
            conn.execute(
                text(
                    "UPDATE notifications SET read_at = :t WHERE "
                    "notifiable_id = :nid AND notifiable_type IN (:ntype, :legacy) AND read_at IS NULL"
                ),
                {"t": now, "nid": notifiable_id, "ntype": notifiable_type, "legacy": legacy_type},
            )

    def mark_notification_read(self, notification_id: str) -> None:
        """Mark one of this notifiable's notifications as read by its ID.

        The update is scoped to this notifiable: an ID belonging to someone
        else's notification is left untouched.
        """
        import time

        from sqlalchemy import text

        from hunt.database.connection import connection, use_connection

        notifiable_id = self.route_notification_for_database()
        notifiable_type, legacy_type = type_names(type(self))
        now = int(time.time())
        with use_connection(connection()) as conn:
            conn.execute(
                text(
                    "UPDATE notifications SET read_at = :t WHERE id = :id "
                    "AND notifiable_id = :nid AND notifiable_type IN (:ntype, :legacy) AND read_at IS NULL"
                ),
                {
                    "t": now,
                    "id": notification_id,
                    "nid": notifiable_id,
                    "ntype": notifiable_type,
                    "legacy": legacy_type,
                },
            )


def _fetch_notifications(
    notifiable: Any,
    unread_only: bool = False,
    read_only: bool = False,
) -> list[dict]:
    import json

    from sqlalchemy import text

    from hunt.database.connection import connection, use_connection

    notifiable_id = notifiable.route_notification_for_database()
    # Rows written before type names were fully qualified hold the bare class name.
    notifiable_type, legacy_type = type_names(type(notifiable))

    where = "notifiable_id = :nid AND notifiable_type IN (:ntype, :legacy)"
    if unread_only:
        where += " AND read_at IS NULL"
    elif read_only:
        where += " AND read_at IS NOT NULL"

    try:
        with use_connection(connection()) as conn:
            result = conn.execute(
                text(f"SELECT * FROM notifications WHERE {where} ORDER BY created_at DESC"),
                {"nid": notifiable_id, "ntype": notifiable_type, "legacy": legacy_type},
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
        """Deliver through every channel, queueing when the notification asks for it."""
        self._dispatch(bool(getattr(self._notification, "should_queue", False)))

    def send_now(self) -> None:
        """Deliver through every channel immediately, never through the queue."""
        self._dispatch(False)

    def _dispatch(self, should_queue: bool) -> None:
        channels = self._notification.via(self._notifiable)
        for channel in channels:
            if should_queue:
                self._queue_channel(channel)
            else:
                driver = self._resolve(channel)
                if driver:
                    driver.send(self._notifiable, self._notification)

    def _queue_channel(self, channel: str) -> None:
        # Serialization problems (non-model notifiable, non-JSON-safe
        # attributes) surface here, at dispatch time.
        job = _SendNotificationJob(self._notification, self._notifiable, channel)
        from hunt.queue.manager import Queue

        try:
            Queue.push(job)
        except Exception as exc:
            from hunt.mail.manager import _runs_jobs_inline

            if _runs_jobs_inline(Queue):
                # The sync driver already ran the job: delivery itself failed.
                raise
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

    The public ``payload`` attribute is a JSON string, so the job is JSON-safe
    on every queue driver::

        {
            "notification": {"class": "app.notifications.InvoiceReady", "state": {...}},
            "notifiable": {"class": "app.models.user.User", "key": 7},
        }

    The notification is its class path plus instance attributes (tagged JSON
    codec; models become class + primary key).  The notifiable is never
    copied into the queue: only ``(model class, primary key)`` is stored and
    the worker re-fetches the row.  Nothing is pickled, and the worker only
    loads ``Notification`` / ``Model`` subclasses.
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
            from hunt.mail._queue_payload import dump_json, dump_object, is_model, model_reference

            if not is_model(notifiable):
                raise TypeError(
                    f"Cannot queue {type(notification).__name__} for a {type(notifiable).__name__}: queued "
                    "notifications store the notifiable as (model class, primary key) and re-fetch it in the "
                    "worker, so it must be a saved Model. Use notify_now() to deliver to other objects."
                )
            payload = dump_json({"notification": dump_object(notification), "notifiable": model_reference(notifiable)})
        self.payload = payload
        self.channel = channel

    def _load(self) -> tuple[Any, Any]:
        from hunt.mail._queue_payload import load_json, load_model, load_object
        from hunt.notifications.notification import Notification

        data = load_json(self.payload, "notification")
        notification = load_object(data.get("notification"), Notification)
        notifiable = load_model(data.get("notifiable"))
        return notification, notifiable

    def handle(self) -> None:
        notification, notifiable = self._load()
        driver = _NotificationSender(notification, notifiable)._resolve(self.channel)
        if driver:
            driver.send(notifiable, notification)

    def failed(self, exc: Exception) -> None:
        pass
