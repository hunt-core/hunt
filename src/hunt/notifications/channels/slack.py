from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
from urllib.request import Request as _Req

if TYPE_CHECKING:
    from hunt.notifications.notification import Notification


class SlackMessage:
    """Fluent builder for Slack webhook messages."""

    def __init__(self) -> None:
        self._text: str = ""
        self._webhook_url: str | None = None
        self._blocks: list[dict] = []
        self._attachments: list[dict] = []
        self._username: str | None = None
        self._icon_emoji: str | None = None
        self._channel: str | None = None

    def to(self, webhook_url: str) -> SlackMessage:
        self._webhook_url = webhook_url
        return self

    def content(self, text: str) -> SlackMessage:
        self._text = text
        return self

    def block(self, block: dict) -> SlackMessage:
        self._blocks.append(block)
        return self

    def attachment(self, attachment: dict) -> SlackMessage:
        self._attachments.append(attachment)
        return self

    def from_(self, username: str) -> SlackMessage:
        self._username = username
        return self

    def icon(self, emoji: str) -> SlackMessage:
        self._icon_emoji = emoji
        return self

    def channel(self, channel: str) -> SlackMessage:
        self._channel = channel
        return self

    def to_payload(self) -> dict:
        payload: dict[str, Any] = {}
        if self._text:
            payload["text"] = self._text
        if self._blocks:
            payload["blocks"] = self._blocks
        if self._attachments:
            payload["attachments"] = self._attachments
        if self._username:
            payload["username"] = self._username
        if self._icon_emoji:
            payload["icon_emoji"] = self._icon_emoji
        if self._channel:
            payload["channel"] = self._channel
        return payload


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects: a webhook that redirects could be pointed at an internal URL."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise HTTPError(req.full_url, code, f"Slack webhook redirect to {newurl!r} refused", headers, fp)


# Module-level so tests can replace it; never follows redirects.
_opener = build_opener(_NoRedirect)


class SlackChannel:
    """Delivers notifications via a Slack incoming webhook.

    The webhook URL must use ``https``.  To also restrict the host (recommended
    whenever users can influence the URL), set ``SlackChannel.allowed_hosts``
    or the ``SLACK_WEBHOOK_ALLOWED_HOSTS`` env var (comma separated), e.g.
    ``hooks.slack.com``.  Redirects are not followed.

    Delivery failures are logged and re-raised so a queued notification is
    retried instead of being recorded as delivered.
    """

    # Hosts webhooks may be sent to; ``None`` allows any https host.
    allowed_hosts: ClassVar[tuple[str, ...] | None] = None
    timeout: ClassVar[float] = 10

    @classmethod
    def _allowed_hosts(cls) -> tuple[str, ...] | None:
        if cls.allowed_hosts is not None:
            return tuple(h.strip().lower() for h in cls.allowed_hosts if h.strip())
        env = os.environ.get("SLACK_WEBHOOK_ALLOWED_HOSTS", "")
        hosts = tuple(h.strip().lower() for h in env.split(",") if h.strip())
        return hosts or None

    @classmethod
    def validate_webhook(cls, webhook: str) -> str:
        """Return *webhook* if it is an acceptable target, else raise ``ValueError``."""
        if not isinstance(webhook, str) or any(c in webhook for c in "\r\n\t "):
            raise ValueError("Slack webhook URL is not a valid URL.")
        parts = urlsplit(webhook)
        host = (parts.hostname or "").lower()
        if parts.scheme.lower() != "https" or not host:
            raise ValueError("Slack webhook URL must be an https:// URL.")
        allowed = cls._allowed_hosts()
        if allowed is not None and host not in allowed:
            raise ValueError(f"Slack webhook host {host!r} is not in the allowed hosts list.")
        return webhook

    def send(self, notifiable: Any, notification: Notification) -> None:
        message: SlackMessage = notification.to_slack(notifiable)  # type: ignore[attr-defined]

        webhook = message._webhook_url
        if not webhook and hasattr(notifiable, "route_notification_for_slack"):
            webhook = notifiable.route_notification_for_slack()
        if not webhook:
            return

        try:
            self.validate_webhook(webhook)
            payload = json.dumps(message.to_payload()).encode()
            req = _Req(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with _opener.open(req, timeout=self.timeout):
                pass
        except Exception as exc:
            try:
                from hunt.log.manager import Log

                # The webhook path is a secret: log only the host.
                host = urlsplit(webhook).hostname if isinstance(webhook, str) else None
                Log.error("Slack notification delivery failed", host=host, error=type(exc).__name__)
            except Exception:
                pass
            raise
