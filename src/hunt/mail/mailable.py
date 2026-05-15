from __future__ import annotations

from typing import Any


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

    # ------------------------------------------------------------------
    # Address fluents
    # ------------------------------------------------------------------

    def to(self, address: str | list[str]) -> Mailable:
        if isinstance(address, str):
            self._to_addresses.append(address)
        else:
            self._to_addresses.extend(address)
        return self

    def cc(self, address: str | list[str]) -> Mailable:
        if isinstance(address, str):
            self._cc_addresses.append(address)
        else:
            self._cc_addresses.extend(address)
        return self

    def bcc(self, address: str | list[str]) -> Mailable:
        if isinstance(address, str):
            self._bcc_addresses.append(address)
        else:
            self._bcc_addresses.extend(address)
        return self

    def reply_to(self, address: str | list[str]) -> Mailable:
        if isinstance(address, str):
            self._reply_to_addresses.append(address)
        else:
            self._reply_to_addresses.extend(address)
        return self

    def from_address(self, address: str, name: str = "") -> Mailable:
        self._from_addr = address
        self._from_name = name
        return self

    # ------------------------------------------------------------------
    # Content fluents
    # ------------------------------------------------------------------

    def subject(self, subject: str) -> Mailable:
        self._subject_line = subject
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

    def attach(self, file: str, options: dict | None = None) -> Mailable:
        self._attachments.append({"file": file, **(options or {})})
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

    def render(self) -> str:
        """Return the HTML body string."""
        if self._html_content is not None:
            return self._html_content
        if self._view_name:
            try:
                from hunt.support.helpers import view as make_view

                rendered = make_view(self._view_name, self._view_data)
                return str(rendered)
            except Exception:
                pass
        return self._text_content or ""

    def _build_and_prepare(self, global_from: tuple[str, str] | None = None) -> Mailable:
        """Run build() and fill defaults from global config."""
        self.build()
        if global_from and not self._from_addr:
            self._from_addr, self._from_name = global_from
        return self
