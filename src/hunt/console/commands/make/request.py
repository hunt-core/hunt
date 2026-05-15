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


@click.command("make:request")
@click.argument("name")
def make_request_command(name: str) -> None:
    """Create a new Form Request class."""
    class_name = name if name.endswith("Request") else f"{name}Request"
    from hunt.support.str import Str

    out = Path.cwd() / "app" / "requests" / f"{Str.snake(class_name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(_STUB.format(class_name=class_name))
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
