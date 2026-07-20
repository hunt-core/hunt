from __future__ import annotations

from pathlib import Path

import click

_STUB = """\
from __future__ import annotations

from hunt.validation.form_request import FormRequest


class {class_name}(FormRequest):
    def authorize(self) -> bool:
        return True

    def rules(self) -> dict:
        return {{
            # "field": "required|string",
        }}
"""


def _create_form_request(name: str) -> None:
    from hunt.console.commands.make import load_stub
    from hunt.support.str import Str

    class_name = name if name.endswith("Request") else f"{name}Request"
    out = Path.cwd() / "app" / "requests" / f"{Str.snake(class_name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(load_stub("request", _STUB).format(class_name=class_name), encoding="utf-8")
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")


@click.command("make:request")
@click.argument("name")
def make_request_command(name: str) -> None:
    """Create a new Form Request class."""
    _create_form_request(name)


@click.command("make:form")
@click.argument("name")
def make_form_command(name: str) -> None:
    """Create a new Form Request class (alias for make:request)."""
    _create_form_request(name)
