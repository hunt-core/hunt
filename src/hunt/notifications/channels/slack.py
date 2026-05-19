from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.request import Request as _Req
from urllib.request import urlopen

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


class SlackChannel:
    """Delivers notifications via a Slack incoming webhook."""

    def send(self, notifiable: Any, notification: Notification) -> None:
        message: SlackMessage = notification.to_slack(notifiable)  # type: ignore[attr-defined]

        webhook = message._webhook_url
        if not webhook and hasattr(notifiable, "route_notification_for_slack"):
            webhook = notifiable.route_notification_for_slack()
        if not webhook:
            return

        payload = json.dumps(message.to_payload()).encode()
        req = _Req(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=10):
                pass
        except Exception:
            try:
                from hunt.log.manager import Log

                Log.error("Slack notification delivery failed", webhook=webhook[:40])
            except Exception:
                pass
