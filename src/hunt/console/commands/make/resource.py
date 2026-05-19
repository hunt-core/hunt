from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str

_STUB = '''\
from __future__ import annotations

from typing import Any


class {class_name}:
    """API resource transformer."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def to_array(self) -> dict:
        return {{
            "id": self.model.id,
        }}

    @classmethod
    def collection(cls, models) -> list[dict]:
        return [cls(m).to_array() for m in models]
'''


@click.command("make:resource")
@click.argument("name")
@click.option("-c", "--collection", "is_collection", is_flag=True, help="Generate a collection resource instead")
def make_resource_command(name: str, is_collection: bool) -> None:
    """Create a new API resource transformer class."""
    from hunt.console.commands.make import load_stub

    class_name = Str.pascal(Str.snake(name))
    content = load_stub("resource", _STUB).format(class_name=class_name)

    out = Path.cwd() / "app" / "resources" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(content)
    click.echo(f"  Created Resource: {out.relative_to(Path.cwd())}")
