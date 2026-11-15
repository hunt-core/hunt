from __future__ import annotations

from email.utils import getaddresses
from typing import Any


def _clean_header(value: Any, field: str) -> str:
    """Return *value* as a header string, rejecting CR/LF (header injection)."""
    if not isinstance(value, str):
        raise TypeError(f"Mail {field} must be a string, got {type(value).__name__}.")
    if "\r" in value or "\n" in value:
        raise ValueError(f"Mail {field} must not contain line breaks.")
    return value


def _clean_address(value: Any, field: str) -> str:
    """Validate that *value* is exactly one email address (``a@b`` or ``Name <a@b>``)."""
    value = _clean_header(value, field).strip()
    if not value:
        raise ValueError(f"Mail {field} address must not be empty.")
    try:
        parsed = [(name, addr) for name, addr in getaddresses([value]) if name or addr]
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid mail {field} address: {value!r}") from exc
    if len(parsed) != 1:
        raise ValueError(f"Mail {field} must be a single address per value, got {value!r}. Pass a list instead.")
    addr = parsed[0][1]
    if not addr or " " in addr or addr.count("@") > 1 or addr.startswith("@") or addr.endswith("@"):
        raise ValueError(f"Invalid mail {field} address: {value!r}")
    return value


def _clean_addresses(address: str | list[str] | tuple[str, ...], field: str) -> list[str]:
    values = [address] if isinstance(address, str) else list(address)
    return [_clean_address(v, field) for v in values]


class Mailable:
    """Base class for all email messages.

    Subclasses override ``build()`` to configure recipients, subject, and body::

        class WelcomeEmail(Mailable):
            def __init__(self, user):
                super().__init__()
                self.user = user

            def build(self):
                return (
                    self.subject("Welcome!")
                        .view("emails.welcome", {"user": self.user})
                )

    Header values (subject, addresses, attachment names) are validated when
    set: line breaks raise ``ValueError`` and each address value must be a
    single address.

    Queueing: ``Mail.queue()`` stores the class path and the instance
    attributes as JSON.  Attributes must be JSON-safe values, dates, decimals,
    bytes or saved models (stored as class + primary key and re-fetched by the
    worker).  Override ``to_queue_payload()`` / ``from_queue_payload()`` for
    anything else.
    """

    def __init__(self) -> None:
        self._to_addresses: list[str] = []
        self._cc_addresses: list[str] = []
        self._bcc_addresses: list[str] = []
        self._reply_to_addresses: list[str] = []
        self._subject_line: str = ""
        self._view_name: str | None = None
        self._view_data: dict[str, Any] = {}
        self._html_content: str | None = None
        self._text_content: str | None = None
        self._attachments: list[dict] = []
        self._from_addr: str | None = None
        self._from_name: str | None = None
        self._mailer_override: str | None = None

    # ------------------------------------------------------------------
    # Address fluents
    # ------------------------------------------------------------------

    def to(self, address: str | list[str]) -> Mailable:
        self._to_addresses.extend(_clean_addresses(address, "To"))
        return self

    def cc(self, address: str | list[str]) -> Mailable:
        self._cc_addresses.extend(_clean_addresses(address, "Cc"))
        return self

    def bcc(self, address: str | list[str]) -> Mailable:
        self._bcc_addresses.extend(_clean_addresses(address, "Bcc"))
        return self

    def reply_to(self, address: str | list[str]) -> Mailable:
        self._reply_to_addresses.extend(_clean_addresses(address, "Reply-To"))
        return self

    def from_address(self, address: str, name: str = "") -> Mailable:
        self._from_addr = _clean_address(address, "From")
        self._from_name = _clean_header(name or "", "From name")
        return self

    # ------------------------------------------------------------------
    # Content fluents
    # ------------------------------------------------------------------

    def subject(self, subject: str) -> Mailable:
        self._subject_line = _clean_header(subject, "subject")
        return self

    def view(self, template: str, data: dict | None = None) -> Mailable:
        self._view_name = template
        self._view_data = data or {}
        return self

    def html(self, content: str) -> Mailable:
        self._html_content = content
        return self

    def text(self, content: str) -> Mailable:
        self._text_content = content
        return self

    def with_mailer(self, mailer: str) -> Mailable:
        """Override which configured mailer to use for this mailable."""
        self._mailer_override: str | None = mailer
        return self

    def attach(self, file: str, options: dict | None = None) -> Mailable:
        """Attach a file from disk.

        Options: ``as`` (or ``name``) sets the attachment filename, ``mime``
        sets its content type.  A missing file raises ``FileNotFoundError``
        when the message is built.
        """
        options = dict(options or {})
        for key in ("as", "name", "mime"):
            if options.get(key) is not None:
                _clean_header(options[key], f"attachment {key}")
        self._attachments.append({"file": str(file), **options})
        return self

    def attach_data(self, data: bytes | str, name: str, mime_type: str = "application/octet-stream") -> Mailable:
        """Attach raw bytes or a string without needing a file on disk."""
        if isinstance(data, str):
            data = data.encode()
        _clean_header(name, "attachment name")
        _clean_header(mime_type, "attachment mime type")
        self._attachments.append({"_raw_data": data, "_raw_name": name, "_raw_mime": mime_type})
        return self

    # ------------------------------------------------------------------
    # Build hook — override in subclasses
    # ------------------------------------------------------------------

    def build(self) -> Mailable:
        """Override to configure the mailable. Called before sending."""
        return self

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def has_html(self) -> bool:
        """True when this mailable has a real HTML body (``html()``, a view, or a custom ``render()``)."""
        return self._html_content is not None or bool(self._view_name) or type(self).render is not Mailable.render

    def render(self) -> str:
        """Return the body string: the HTML body, or the text body for text-only mail.

        A template that fails to render raises; the mail is not sent.
        """
        if self._html_content is not None:
            return self._html_content
        if self._view_name:
            from hunt.support.helpers import view as make_view

            return str(make_view(self._view_name, self._view_data))
        return self._text_content or ""

    def _build_and_prepare(self, global_from: tuple[str, str] | None = None) -> Mailable:
        """Run build() and fill defaults from global config."""
        self.build()
        if global_from and not self._from_addr:
            self._from_addr, self._from_name = global_from
        return self

    # ------------------------------------------------------------------
    # Queue serialization
    # ------------------------------------------------------------------

    def to_queue_payload(self) -> dict[str, Any]:
        """Return this mailable's state as a JSON-safe dict for the queue."""
        from hunt.mail._queue_payload import encode_state

        return encode_state(self)

    @classmethod
    def from_queue_payload(cls, state: dict[str, Any]) -> Mailable:
        """Rebuild a mailable from ``to_queue_payload()`` output (worker side, ``__init__`` is not called)."""
        from hunt.mail._queue_payload import decode_state

        obj = cls.__new__(cls)
        obj.__dict__.update(decode_state(state))
        return obj
