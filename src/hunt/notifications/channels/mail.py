from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hunt.mail.mailable import Mailable
from hunt.mail.manager import Mail

if TYPE_CHECKING:
    from hunt.notifications.notification import Notification


class MailChannel:
    """Delivers notifications via the Mail system."""

    def send(self, notifiable: Any, notification: Notification) -> None:

        mailable = notification.to_mail(notifiable)

        # If no To address is set, route via notifiable
        if isinstance(mailable, Mailable) and not mailable._to_addresses:
            address = self._route(notifiable)
            if address:
                mailable.to(address)

        Mail.send(mailable)

    @staticmethod
    def _route(notifiable: Any) -> str | None:
        """Ask the notifiable where to deliver mail."""
        if hasattr(notifiable, "route_notification_for_mail"):
            return notifiable.route_notification_for_mail()
        # Fall back to email attribute
        if hasattr(notifiable, "_attributes"):
            return notifiable._attributes.get("email")
        return getattr(notifiable, "email", None)
