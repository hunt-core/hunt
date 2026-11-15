from __future__ import annotations

import asyncio
import mimetypes
import os
import smtplib
import ssl
from collections.abc import Callable
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from hunt.mail.mailable import Mailable, _clean_address, _clean_header
from hunt.queue.job import Job

try:
    from hunt.log.manager import Log
except Exception:
    import logging as _logging

    Log = _logging.getLogger("hunt.mail")  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------

_ENCRYPTION_MODES = {
    "ssl": "ssl",
    "smtps": "ssl",
    "tls": "starttls",
    "starttls": "starttls",
    "none": "none",
}


def _normalise_encryption(value: Any) -> str:
    """Map a configured ``encryption`` value to ``ssl``, ``starttls`` or ``none``.

    Matching is case-insensitive.  Anything else (including an empty string)
    raises, so a typo can never silently downgrade the connection to plaintext.
    """
    mode = _ENCRYPTION_MODES.get(value.strip().lower()) if isinstance(value, str) else None
    if mode is None:
        raise ValueError(
            f"Unsupported mail encryption {value!r}. Use 'ssl', 'tls' (STARTTLS), 'starttls', "
            "or 'none' to explicitly send without encryption."
        )
    return mode


def _addr_spec(value: str) -> str:
    return parseaddr(value)[1]


class _SmtpDriver:
    """Sends through SMTP.

    Config keys: host, port, username, password, encryption (``ssl``, ``tls`` /
    ``starttls`` or ``none``; default ``tls``) and timeout in seconds (default 30).
    The call blocks; use ``await Mail.send_async(...)`` inside request handlers.
    """

    def send(self, mailable: Mailable, config: dict) -> None:
        host = config.get("host", "127.0.0.1")
        port = int(config.get("port", 587))
        username = config.get("username")
        password = config.get("password")
        encryption = _normalise_encryption(config.get("encryption", "tls"))
        timeout = float(config.get("timeout", 30))

        msg = _build_mime(mailable)
        # Bcc recipients are part of the SMTP envelope only, never the headers.
        recipients = [
            _addr_spec(a) for a in (*mailable._to_addresses, *mailable._cc_addresses, *mailable._bcc_addresses)
        ]
        if not recipients:
            raise ValueError("Cannot send mail without any recipient (to, cc or bcc).")
        sender = _addr_spec(mailable._from_addr or "")

        if encryption == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)

        with server:
            if encryption == "starttls":
                # Raises if the server does not offer STARTTLS: never fall back to plaintext.
                server.starttls(context=ssl.create_default_context())
            if username and password:
                if encryption == "none":
                    # Only reachable when the app explicitly opted out of encryption.
                    Log.warning("Mail: authenticating to SMTP without encryption (encryption is 'none').")
                server.login(username, password)
            server.send_message(msg, from_addr=sender, to_addrs=recipients)


class _LogDriver:
    def send(self, mailable: Mailable, config: dict) -> None:
        # Bodies can contain password-reset links. Unless configured either
        # way, they are written only in local development (APP_ENV=local), so
        # a fresh app shows the link while a deployed one never logs it.
        log_body = config.get("log_body")
        if log_body is None:
            log_body = {"true": True, "false": False}.get(os.environ.get("MAIL_LOG_BODY", "").strip().lower())
        if log_body is None:
            from hunt.application import app_env

            log_body = app_env().lower() == "local"
        lines = [
            "=" * 60,
            f"To:      {', '.join(mailable._to_addresses)}",
        ]
        if mailable._cc_addresses:
            lines.append(f"Cc:      {', '.join(mailable._cc_addresses)}")
        if mailable._bcc_addresses:
            lines.append(f"Bcc:     {', '.join(mailable._bcc_addresses)}")
        lines += [
            f"Subject: {mailable._subject_line}",
            f"From:    {mailable._from_name} <{mailable._from_addr}>",
        ]
        if log_body:
            body = mailable.render()
            lines += ["-" * 60, body[:2000] + ("..." if len(body) > 2000 else "")]
        lines.append("=" * 60)
        Log.info("\n".join(lines))


class _ArrayDriver:
    """Stores sent mailables in memory — used by the ``array`` transport."""

    def __init__(self) -> None:
        self._outbox: list[Mailable] = []

    def send(self, mailable: Mailable, config: dict) -> None:
        self._outbox.append(mailable)

    def sent(self) -> list[Mailable]:
        return list(self._outbox)

    def flush(self) -> None:
        self._outbox.clear()


def _address(value: str, field: str, display_name: str | None = None) -> Address:
    """Build a header ``Address`` from one validated address value."""
    name, addr = parseaddr(_clean_address(value, field))
    if display_name:
        name = _clean_header(display_name, f"{field} name")
    try:
        if "@" not in addr:
            return Address(display_name=name, username=addr)
        return Address(display_name=name, addr_spec=addr)
    except Exception as exc:
        raise ValueError(f"Invalid mail {field} address: {value!r}") from exc


def _has_html(mailable: Mailable) -> bool:
    has_html = getattr(mailable, "has_html", None)
    if callable(has_html):
        return bool(has_html())
    return getattr(mailable, "_html_content", None) is not None or bool(getattr(mailable, "_view_name", None))


def _build_mime(mailable: Mailable) -> EmailMessage:
    """Build the MIME message for *mailable*.

    Every header value is validated (no CR/LF, one address per value) even if
    it was assigned without going through the fluent setters.  Bcc is never
    written to the headers.  An HTML part is only added when the mailable has
    real HTML; plain-text mail stays ``text/plain``.  Rendering errors and
    missing attachment files raise instead of sending a broken message.
    """
    text_body = mailable._text_content
    html_body = mailable.render() if _has_html(mailable) else None

    msg = EmailMessage()
    msg["Subject"] = _clean_header(mailable._subject_line or "", "subject")
    if mailable._from_addr:
        msg["From"] = _address(mailable._from_addr, "From", mailable._from_name)
    if mailable._to_addresses:
        msg["To"] = [_address(a, "To") for a in mailable._to_addresses]
    if mailable._cc_addresses:
        msg["Cc"] = [_address(a, "Cc") for a in mailable._cc_addresses]
    if mailable._reply_to_addresses:
        msg["Reply-To"] = [_address(a, "Reply-To") for a in mailable._reply_to_addresses]
    for a in mailable._bcc_addresses:
        _address(a, "Bcc")  # validated, deliberately not added as a header

    if text_body and html_body:
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
    elif html_body:
        msg.set_content(html_body, subtype="html")
    else:
        msg.set_content(text_body or "")

    for att in mailable._attachments:
        if "_raw_data" in att:
            raw = att["_raw_data"]
            data = raw if isinstance(raw, bytes) else str(raw).encode()
            name = att["_raw_name"]
            mime_type = att.get("_raw_mime") or "application/octet-stream"
        else:
            path = Path(att["file"])
            if not path.is_file():
                raise FileNotFoundError(f"Mail attachment not found: {path}")
            data = path.read_bytes()
            name = att.get("as") or att.get("name") or path.name
            mime_type = att.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        maintype, _, subtype = _clean_header(mime_type, "attachment mime type").partition("/")
        # add_attachment sets Content-Disposition through add_header, which
        # quotes/encodes the filename parameter correctly.
        msg.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=_clean_header(name, "attachment name"),
        )

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

    async def send_async(self, mailable: Mailable) -> None:
        """Send without blocking the event loop (runs the transport in a thread)."""
        await self._manager.send_async(self._apply(mailable))

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
    """Replaces the real mail transport during tests.

    Returned by ``Mail.fake()``.  It is active immediately; use it as a context
    manager to guarantee the real transport is restored::

        with Mail.fake() as fake:
            Mail.to("user@example.com").send(WelcomeEmail())
            fake.assert_sent(WelcomeEmail)

    Both sent and queued mailables are recorded (``Mail.queue()`` / ``later()``
    do not touch the queue while faking); queued ones are also listed by
    ``queued()``.
    """

    def __init__(self, manager: _MailManager | None = None) -> None:
        self._manager = manager
        self._sent: list[Mailable] = []
        self._queued: list[Mailable] = []

    def __enter__(self) -> _MailFake:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._manager is not None and self._manager._fake is self:
            self._manager.stop_faking()

    def _record(self, mailable: Mailable, queued: bool = False) -> None:
        self._sent.append(mailable)
        if queued:
            self._queued.append(mailable)

    def sent(self, mailable_class: type | None = None) -> list[Mailable]:
        if mailable_class is None:
            return list(self._sent)
        return [m for m in self._sent if isinstance(m, mailable_class)]

    def queued(self, mailable_class: type | None = None) -> list[Mailable]:
        if mailable_class is None:
            return list(self._queued)
        return [m for m in self._queued if isinstance(m, mailable_class)]

    def sent_to(self, address: str, mailable_class: type | None = None) -> list[Mailable]:
        """Mailables whose to, cc or bcc recipients include *address*."""
        wanted = _addr_spec(address).lower() or address.lower()
        matches = []
        for m in self.sent(mailable_class):
            recipients = (*m._to_addresses, *m._cc_addresses, *m._bcc_addresses)
            if any((_addr_spec(r).lower() or r.lower()) == wanted for r in recipients):
                matches.append(m)
        return matches

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

    def assert_queued(self, mailable_class: type, count: int | None = None) -> None:
        matches = self.queued(mailable_class)
        if count is not None:
            assert len(matches) == count, (
                f"Expected {mailable_class.__name__} to be queued {count} time(s), "
                f"but it was queued {len(matches)} time(s)."
            )
        else:
            assert matches, f"Expected {mailable_class.__name__} to be queued, but it was not."

    def assert_sent_to(self, address: str, mailable_class: type | None = None) -> None:
        what = mailable_class.__name__ if mailable_class else "mail"
        assert self.sent_to(address, mailable_class), f"Expected {what} to be sent to {address!r}, but it was not."

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


class _SendMailableJob(Job):
    """Wraps a Mailable so it can be pushed to the queue.

    The public ``payload`` attribute is a JSON string, so the job is JSON-safe
    on every queue driver::

        {"class": "app.mail.welcome.WelcomeEmail", "state": {...}}

    ``state`` comes from ``Mailable.to_queue_payload()`` (instance attributes
    through the tagged JSON codec; models become class + primary key and are
    re-fetched).  The worker only imports ``class`` if it is a ``Mailable``
    subclass and rebuilds it with ``from_queue_payload()``.  Nothing is pickled.
    """

    queue: str = "default"
    tries: int = 3
    backoff: int = 0

    def __init__(self, mailable: Mailable | None = None, payload: str = "") -> None:
        if mailable is not None:
            from hunt.mail._queue_payload import class_path, dump_json

            payload = dump_json({"class": class_path(type(mailable)), "state": mailable.to_queue_payload()})
        self.payload = payload

    def mailable(self) -> Mailable:
        """Rebuild the mailable described by the payload."""
        from hunt.mail._queue_payload import import_class, load_json

        data = load_json(self.payload, "mail")
        cls = import_class(data.get("class", ""), Mailable)
        return cls.from_queue_payload(data.get("state", {}))  # type: ignore[attr-defined]

    def handle(self) -> None:
        Mail.send(self.mailable())

    def failed(self, exc: Exception) -> None:
        pass


# ---------------------------------------------------------------------------
# Mail manager
# ---------------------------------------------------------------------------


def _env_config() -> dict[str, Any]:
    """Mail config built from the MAIL_* env vars (same shape as config/mail.py)."""
    return {
        "default": os.environ.get("MAIL_MAILER", "log"),
        "mailers": {
            "smtp": {
                "transport": "smtp",
                "host": os.environ.get("MAIL_HOST", "127.0.0.1"),
                "port": int(os.environ.get("MAIL_PORT", "587")),
                "username": os.environ.get("MAIL_USERNAME") or None,
                "password": os.environ.get("MAIL_PASSWORD") or None,
                "encryption": os.environ.get("MAIL_ENCRYPTION", "tls"),
            },
            "log": {"transport": "log"},
            "array": {"transport": "array"},
        },
        "from": {
            "address": os.environ.get("MAIL_FROM_ADDRESS", ""),
            "name": os.environ.get("MAIL_FROM_NAME", ""),
        },
    }


def _runs_jobs_inline(queue_manager: Any) -> bool:
    """True when the configured queue driver executes jobs during ``push`` (sync driver)."""
    try:
        return type(queue_manager._get_driver()).__name__ == "SyncDriver"
    except Exception:
        return False


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

        # Async request handlers (does not block the event loop)
        await Mail.to("user@example.com").send_async(WelcomeEmail(user))

        # Testing
        with Mail.fake() as fake:
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
        self._configured = False

    def configure(self, config: dict[str, Any]) -> None:
        self._configured = True
        self._config = config
        self._default = config.get("default", config.get("driver", "log"))
        from_cfg = config.get("from", {})
        self._from_address = from_cfg.get("address", config.get("from_address", ""))
        self._from_name = from_cfg.get("name", config.get("from_name", ""))

    def _ensure_configured(self) -> None:
        """Fall back to MAIL_* env vars when configure() was never called."""
        if not self._configured:
            self.configure(_env_config())

    def to(self, address: str | list[str]) -> _PendingMail:
        addresses = [address] if isinstance(address, str) else list(address)
        return _PendingMail(self, addresses)

    def send(self, mailable: Mailable) -> None:
        """Build and send the mailable now.

        This blocks until the transport is done (an SMTP conversation can take
        seconds); in async code use ``await Mail.send_async(mailable)``.
        Errors from rendering, attachments or the transport propagate.
        """
        self._ensure_configured()
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

    async def send_async(self, mailable: Mailable) -> None:
        """Send from async code without blocking the event loop.

        The mailable is built and handed to the transport in a worker thread
        (``asyncio.to_thread``).
        """
        await asyncio.to_thread(self.send, mailable)

    def queue(self, mailable: Mailable) -> None:
        """Queue mailable via the queue system (falls back to sync send if the queue backend fails)."""
        self._dispatch_queued(mailable, None)

    def later(self, delay: int, mailable: Mailable) -> None:
        """Queue a mailable to be sent after ``delay`` seconds."""
        self._dispatch_queued(mailable, delay)

    def _dispatch_queued(self, mailable: Mailable, delay: int | None) -> None:
        if self._fake is not None:
            # While faking nothing may reach a real queue or transport.
            self._ensure_configured()
            mailable._build_and_prepare((self._from_address, self._from_name))
            self._fake._record(mailable, queued=True)
            return

        # Serialization problems (non-JSON-safe attributes) surface here, at dispatch time.
        job = _SendMailableJob(mailable)
        from hunt.queue.manager import Queue

        try:
            if delay is None:
                Queue.push(job)
            else:
                Queue.later(delay, job)
        except Exception as exc:
            if _runs_jobs_inline(Queue):
                # The sync driver already ran the job: the failure is the send
                # itself (render error, SMTP failure), not the queue. Do not retry.
                raise
            Log.warning(f"Queueing mailable failed — sending synchronously instead: {exc}")
            self.send(mailable)

    def raw(self, text: str, callback: Callable[[Mailable], None]) -> None:
        """Send a raw text email configured by the callback."""
        m = Mailable()
        m.text(text)
        callback(m)
        self.send(m)

    def fake(self) -> _MailFake:
        """Install a fake transport and return it for assertions.

        The fake is active as soon as this returns.  Prefer the context-manager
        form, which always restores the real transport::

            with Mail.fake() as fake:
                ...
                fake.assert_sent(WelcomeEmail)

        Without ``with`` you must call ``Mail.stop_faking()`` yourself.
        """
        self._fake = _MailFake(self)
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
