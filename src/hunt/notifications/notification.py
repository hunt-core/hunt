from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.mail.mailable import Mailable
    from hunt.mail.message import MailMessage


def qualified_name(cls: type) -> str:
    """Fully-qualified class name (``module.QualName``) used for stored type columns."""
    return f"{cls.__module__}.{cls.__qualname__}"


def type_names(cls: type) -> tuple[str, str]:
    """Return ``(fully-qualified name, legacy bare name)`` for *cls*.

    New rows store the qualified name; rows written by older versions hold the
    bare class name, so lookups match either.
    """
    return qualified_name(cls), cls.__name__


class Notification:
    """Base class for all notifications.

    Subclasses define ``via()`` to pick channels and ``to_{channel}()`` methods
    to provide channel-specific payloads::

        class InvoiceReady(Notification):
            def __init__(self, invoice):
                self.invoice = invoice

            def via(self, notifiable):
                return ["mail", "database"]

            def to_mail(self, notifiable):
                return (
                    MailMessage()
                    .subject("Your invoice is ready")
                    .line(f"Invoice #{self.invoice.id} has been generated.")
                    .action("View Invoice", f"/invoices/{self.invoice.id}")
                )

            def to_database(self, notifiable):
                return {"invoice_id": self.invoice.id}

    Set ``should_queue = True`` to deliver through the queue.  A queued
    notification is stored as its class path plus its instance attributes
    (JSON-safe values, dates, decimals, bytes or saved models), and the
    notifiable as ``(model class, primary key)`` which the worker re-fetches;
    the class must be importable and the notifiable must be a saved model.
    ``notifiable.notify_now(...)`` always delivers immediately.
    """

    def __init__(self) -> None:
        self._id = str(uuid.uuid4())

    @property
    def id(self) -> str:
        return self._id

    def via(self, notifiable: Any) -> list[str]:
        """Return the list of channels to deliver through."""
        return ["mail"]

    def to_mail(self, notifiable: Any) -> Mailable | MailMessage:
        raise NotImplementedError(f"{type(self).__name__} must implement to_mail() when using the mail channel.")

    def to_database(self, notifiable: Any) -> dict:
        raise NotImplementedError(
            f"{type(self).__name__} must implement to_database() when using the database channel."
        )
