from __future__ import annotations

from hunt.mail.mailable import Mailable


class MailMessage(Mailable):
    """Fluent message builder for use inside ``Notification.to_mail()``.

    Example::

        def to_mail(self, notifiable):
            return (
                MailMessage()
                .subject("Your invoice is ready")
                .greeting(f"Hello {notifiable.name}!")
                .line("Your invoice #1234 has been generated.")
                .action("View Invoice", "https://example.com/invoices/1234")
                .line("Thank you for your business!")
            )
    """

    def __init__(self) -> None:
        super().__init__()
        self._greeting: str = "Hello!"
        self._intro_lines: list[str] = []
        self._outro_lines: list[str] = []
        self._action_text: str | None = None
        self._action_url: str | None = None
        self._error: bool = False

    def greeting(self, text: str) -> MailMessage:
        self._greeting = text
        return self

    def line(self, text: str) -> MailMessage:
        self._intro_lines.append(text)
        return self

    def outro(self, text: str) -> MailMessage:
        self._outro_lines.append(text)
        return self

    def action(self, text: str, url: str) -> MailMessage:
        self._action_text = text
        self._action_url = url
        return self

    def error(self) -> MailMessage:
        """Mark this as an error notification (changes action button color)."""
        self._error = True
        return self

    def render(self) -> str:
        """Render lines into a minimal HTML email body."""
        parts: list[str] = [
            "<!DOCTYPE html><html><body style='font-family:sans-serif;max-width:600px;margin:auto;padding:20px'>",
            f"<p><strong>{self._greeting}</strong></p>",
        ]
        for line in self._intro_lines:
            parts.append(f"<p>{line}</p>")
        if self._action_text and self._action_url:
            color = "#e3342f" if self._error else "#3490dc"
            parts.append(
                f"<p><a href='{self._action_url}' "
                f"style='background:{color};color:#fff;padding:10px 20px;"
                f"text-decoration:none;border-radius:4px'>{self._action_text}</a></p>"
            )
        for line in self._outro_lines:
            parts.append(f"<p>{line}</p>")
        parts.append("</body></html>")
        return "".join(parts)
