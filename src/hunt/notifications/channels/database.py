from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from hunt.notifications.notification import qualified_name

if TYPE_CHECKING:
    from hunt.notifications.notification import Notification

TABLE = "notifications"


class DatabaseChannel:
    """Delivers notifications by inserting a row into the notifications table."""

    def send(self, notifiable: Any, notification: Notification) -> None:
        from sqlalchemy import text

        from hunt.database.connection import connection, use_connection

        data = notification.to_database(notifiable)
        notifiable_id = self._get_id(notifiable)
        # Fully-qualified names: two classes called "User" or "Invoice" in
        # different modules must not share rows.
        notifiable_type = qualified_name(type(notifiable))
        now = int(time.time())

        with use_connection(connection()) as conn:
            conn.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(id, type, notifiable_id, notifiable_type, data, read_at, created_at, updated_at) "
                    "VALUES (:id, :type, :nid, :ntype, :data, NULL, :ca, :ua)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "type": qualified_name(type(notification)),
                    "nid": notifiable_id,
                    "ntype": notifiable_type,
                    "data": json.dumps(data),
                    "ca": now,
                    "ua": now,
                },
            )

    @staticmethod
    def _get_id(notifiable: Any) -> Any:
        route = getattr(notifiable, "route_notification_for_database", None)
        if callable(route):
            return route()
        if hasattr(notifiable, "_attributes"):
            return notifiable._attributes.get("id")
        return getattr(notifiable, "id", None)
