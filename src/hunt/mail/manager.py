from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from hunt.mail.mailable import Mailable

try:
    from hunt.log.manager import Log as _Log
except Exception:
    import logging as _logging

    _Log = _logging.getLogger("hunt.mail")  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


class _SmtpDriver:
    def send(self, mailable: Mailable, config: dict) -> None:
        host = config.get("host", "127.0.0.1")
        port = int(config.get("port", 587))
        username = config.get("username")
        password = config.get("password")
        encryption = config.get("encryption", "tls")

        msg = _build_mime(mailable)

        if encryption == "ssl":
            ctx = ssl.create_default_context()
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, context=ctx)
        else:
            server = smtplib.SMTP(host, port)
            if encryption == "tls":
                server.starttls(context=ssl.create_default_context())

        with server:
            if username and password:
                server.login(username, password)
            server.send_message(msg)


Log = _Log


class _LogDriver:
    def send(self, mailable: Mailable, config: dict) -> None:
        log_body = config.get("log_body", False)
        lines = [
            "=" * 60,
            f"To:      {', '.join(mailable._to_addresses)}",
            f"Subject: {mailable._subject_line}",
            f"From:    {mailable._from_name} <{mailable._from_addr}>",
        ]
        if log_body:
            body = mailable.render()
            lines += ["-" * 60, body[:2000] + ("..." if len(body) > 2000 else "")]
        lines.append("=" * 60)
        Log.info("\n".join(lines))


class _ArrayDriver:
    """Stores sent mailables in memory — used by Mail.fake()."""

    def __init__(self) -> None:
        self._outbox: list[Mailable] = []

    def send(self, mailable: Mailable, config: dict) -> None:
        self._outbox.append(mailable)

    def sent(self) -> list[Mailable]:
        return list(self._outbox)

    def flush(self) -> None:
        self._outbox.clear()


def _build_mime(mailable: Mailable) -> MIMEMultipart:
    html_body = mailable.render()
    text_body = mailable._text_content or ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = mailable._subject_line
    from_name = mailable._from_name or ""
    from_addr = mailable._from_addr or ""
    msg["From"] = f"{from_name} <{from_addr}>" if from_name else from_addr
    msg["To"] = ", ".join(mailable._to_addresses)

    if mailable._cc_addresses:
        msg["Cc"] = ", ".join(mailable._cc_addresses)
    if mailable._reply_to_addresses:
        msg["Reply-To"] = ", ".join(mailable._reply_to_addresses)

    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    for att in mailable._attachments:
        if "_raw_data" in att:
            raw = att["_raw_data"]
            name = att["_raw_name"]
            mime_type = att.get("_raw_mime", "application/octet-stream")
            _, _, sub = mime_type.partition("/")
            part = MIMEApplication(
                raw if isinstance(raw, bytes) else raw.encode(), _subtype=sub or "octet-stream", Name=name
            )
            part["Content-Disposition"] = f'attachment; filename="{name}"'
        else:
            path = Path(att["file"])
            if not path.exists():
                continue
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)

    return msg


# ---------------------------------------------------------------------------
# Pending mail (Mail.to(...).send(...))
# ---------------------------------------------------------------------------


class _PendingMail:
    def __init__(self, manager: _MailManager, to: list[str]) -> None:
        self._manager = manager
        self._to = to
        self._cc: list[str] = []
        self._bcc: list[str] = []

    def cc(self, address: str | list[str]) -> _PendingMail:
        if isinstance(address, str):
            self._cc.append(address)
        else:
            self._cc.extend(address)
        return self

    def bcc(self, address: str | list[str]) -> _PendingMail:
        if isinstance(address, str):
            self._bcc.append(address)
        else:
            self._bcc.extend(address)
        return self

    def _apply(self, mailable: Mailable) -> Mailable:
        mailable.to(self._to)
        if self._cc:
            mailable.cc(self._cc)
        if self._bcc:
            mailable.bcc(self._bcc)
        return mailable

    def send(self, mailable: Mailable) -> None:
        self._manager.send(self._apply(mailable))

    def queue(self, mailable: Mailable) -> None:
        """Queue the mailable for background sending (delegates to queue driver)."""
        self._manager.queue(self._apply(mailable))

    def later(self, delay: int, mailable: Mailable) -> None:
        """Queue the mailable with a delay in seconds."""
        self._manager.later(delay, self._apply(mailable))


# ---------------------------------------------------------------------------
# Mail fake
# ---------------------------------------------------------------------------


class _MailFake:
    """Replaces the real mail transport during tests."""

    def __init__(self) -> None:
        self._sent: list[Mailable] = []

    def _record(self, mailable: Mailable) -> None:
        self._sent.append(mailable)

    def sent(self, mailable_class: type | None = None) -> list[Mailable]:
        if mailable_class is None:
            return list(self._sent)
        return [m for m in self._sent if isinstance(m, mailable_class)]

    def assert_sent(
        self,
        mailable_class: type,
        count: int | None = None,
        callback: Callable | None = None,
    ) -> None:
        matches = self.sent(mailable_class)
        if callback:
            matches = [m for m in matches if callback(m)]
        if count is not None:
            assert len(matches) == count, (
                f"Expected {mailable_class.__name__} to be sent {count} time(s), "
                f"but it was sent {len(matches)} time(s)."
            )
        else:
            assert matches, f"Expected {mailable_class.__name__} to be sent, but it was not."

    def assert_not_sent(self, mailable_class: type) -> None:
        matches = self.sent(mailable_class)
        assert not matches, (
            f"Expected {mailable_class.__name__} not to be sent, but it was sent {len(matches)} time(s)."
        )

    def assert_nothing_sent(self) -> None:
        assert not self._sent, f"Expected no mailables to be sent, but {len(self._sent)} were sent."


# ---------------------------------------------------------------------------
# Internal queue job for Mail.queue()
# ---------------------------------------------------------------------------


class _SendMailableJob:
    """Wraps a Mailable so it can be pushed to the queue without a lambda."""

    queue: str = "default"
    tries: int = 3
    backoff: int = 0

    def __init__(self, mailable: Mailable, manager: Any) -> None:
        self._mailable = mailable
        self._manager = manager

    def handle(self) -> None:
        self._manager.send(self._mailable)

    def failed(self, exc: Exception) -> None:
        pass


# ---------------------------------------------------------------------------
# Mail manager
# ---------------------------------------------------------------------------


class _MailManager:
    """Central mail dispatcher — configure once, call everywhere.

    Usage::

        # In bootstrap/app.py
        Mail.configure({
            "default": "log",
            "mailers": {"log": {"transport": "log"}},
            "from": {"address": "hello@example.com", "name": "App"},
        })

        # Sending
        Mail.to("user@example.com").send(WelcomeEmail(user))
        Mail.send(mailable_instance_with_to_already_set)

        # Testing
        fake = Mail.fake()
        # ... do work ...
        fake.assert_sent(WelcomeEmail)
    """

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._default: str = "log"
        self._from_address: str = ""
        self._from_name: str = ""
        self._fake: _MailFake | None = None
        self._array_driver = _ArrayDriver()

    def configure(self, config: dict[str, Any]) -> None:
        self._config = config
        self._default = config.get("default", config.get("driver", "log"))
        from_cfg = config.get("from", {})
        self._from_address = from_cfg.get("address", config.get("from_address", ""))
        self._from_name = from_cfg.get("name", config.get("from_name", ""))

    def to(self, address: str | list[str]) -> _PendingMail:
        addresses = [address] if isinstance(address, str) else list(address)
        return _PendingMail(self, addresses)

    def send(self, mailable: Mailable) -> None:
        global_from = (self._from_address, self._from_name)
        mailable._build_and_prepare(global_from)

        if self._fake is not None:
            self._fake._record(mailable)
            return

        mailer_override = getattr(mailable, "_mailer_override", None)
        if mailer_override and mailer_override != self._default:
            driver_config = self._config.get("mailers", {}).get(mailer_override, self._config)
            driver = self._resolve_driver_for(driver_config)
        else:
            driver_config = self._driver_config()
            driver = self._resolve_driver()
        driver.send(mailable, driver_config)

    def queue(self, mailable: Mailable) -> None:
        """Queue mailable via the queue system (falls back to sync send)."""
        try:
            from hunt.queue.manager import Queue

            Queue.push(_SendMailableJob(mailable, self))
        except Exception:
            self.send(mailable)

    def later(self, delay: int, mailable: Mailable) -> None:
        """Queue a mailable to be sent after ``delay`` seconds."""
        try:
            from hunt.queue.manager import Queue

            Queue.later(delay, _SendMailableJob(mailable, self))
        except Exception:
            self.send(mailable)

    def raw(self, text: str, callback: Callable[[Mailable], None]) -> None:
        """Send a raw text email configured by the callback."""
        m = Mailable()
        m.text(text)
        callback(m)
        self.send(m)

    def fake(self) -> _MailFake:
        """Install a fake transport and return it for assertions."""
        self._fake = _MailFake()
        return self._fake

    def stop_faking(self) -> None:
        self._fake = None

    def sent(self) -> list[Mailable]:
        """Return list of sent mailables (only during faking)."""
        if self._fake:
            return self._fake.sent()
        return self._array_driver.sent()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _driver_config(self) -> dict:
        mailers = self._config.get("mailers", {})
        return mailers.get(self._default, self._config)

    def _resolve_driver(self) -> Any:
        return self._resolve_driver_for(self._driver_config())

    def _resolve_driver_for(self, config: dict) -> Any:
        transport = config.get("transport", self._default)
        if transport == "smtp":
            return _SmtpDriver()
        if transport == "array":
            return self._array_driver
        return _LogDriver()


Mail = _MailManager()
