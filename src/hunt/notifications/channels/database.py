from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.notifications.notification import Notification

TABLE = "notifications"


class DatabaseChannel:
    """Delivers notifications by inserting a row into the notifications table."""

    def send(self, notifiable: Any, notification: Notification) -> None:
        from sqlalchemy import text

        from hunt.database.connection import connection

        data = notification.to_database(notifiable)
        notifiable_id = self._get_id(notifiable)
        notifiable_type = type(notifiable).__name__
        now = int(time.time())

        with connection().connect() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {TABLE} "
                    "(id, type, notifiable_id, notifiable_type, data, read_at, created_at, updated_at) "
                    "VALUES (:id, :type, :nid, :ntype, :data, NULL, :ca, :ua)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "type": type(notification).__name__,
                    "nid": notifiable_id,
                    "ntype": notifiable_type,
                    "data": json.dumps(data),
                    "ca": now,
                    "ua": now,
                },
            )
            conn.commit()

    @staticmethod
    def _get_id(notifiable: Any) -> Any:
        if hasattr(notifiable, "_attributes"):
            return notifiable._attributes.get("id")
        return getattr(notifiable, "id", None)
