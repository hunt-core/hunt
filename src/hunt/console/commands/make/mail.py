from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:mail")
@click.argument("name")
def make_mail_command(name: str) -> None:
    """Create a new Mailable class."""
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._naming import validate_identifier
    from hunt.console.commands.make._output import output

    validate_identifier(name, "mailable name")
    output.configure()
    class_name = Str.pascal(Str.snake(name))
    content = load_stub("mail", _STUB).replace("{{class}}", class_name)

    out = Path.cwd() / "app" / "mail" / f"{Str.snake(name)}.py"
    output.write(out, content, label="Created Mailable")


_STUB = """\
from hunt.mail.mailable import Mailable


class {{class}}(Mailable):
    def __init__(self) -> None:
        super().__init__()

    def build(self) -> "{{class}}":
        return (
            self.subject("Subject here")
                .view("emails.template")
        )
"""
