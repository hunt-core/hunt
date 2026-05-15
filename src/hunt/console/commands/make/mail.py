from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:mail")
@click.argument("name")
def make_mail_command(name: str) -> None:
    """Create a new Mailable class."""
    class_name = Str.pascal(Str.snake(name))
    content = _STUB.replace("{{class}}", class_name)

    out = Path.cwd() / "app" / "mail" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Mailable: {out.relative_to(Path.cwd())}")


_STUB = """\
from hunt.mail.mailable import Mailable


class {{class}}(Mailable):
    def __init__(self) -> None:
        super().__init__()

    def build(self) -> "{{class}}":
        return (
            self.subject("Subject here")
                .view("emails.{{class_lower}}")
        )
""".replace("{{class_lower}}", "")


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
