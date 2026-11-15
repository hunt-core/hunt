from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:notification")
@click.argument("name")
def make_notification_command(name: str) -> None:
    """Create a new Notification class."""
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._naming import validate_identifier
    from hunt.console.commands.make._output import output

    validate_identifier(name, "notification name")
    output.configure()
    class_name = Str.pascal(Str.snake(name))
    content = load_stub("notification", _STUB).replace("{{class}}", class_name)

    out = Path.cwd() / "app" / "notifications" / f"{Str.snake(name)}.py"
    output.write(out, content, label="Created Notification")


_STUB = """\
from hunt.notifications.notification import Notification
from hunt.mail.message import MailMessage


class {{class}}(Notification):
    def __init__(self) -> None:
        super().__init__()

    def via(self, notifiable):
        return ["mail"]

    def to_mail(self, notifiable):
        return (
            MailMessage()
            .subject("Notification Subject")
            .line("A notification has been sent.")
            .action("View", "/")
        )

    def to_database(self, notifiable):
        return {}
"""
