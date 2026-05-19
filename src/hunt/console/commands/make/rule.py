from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str

_STUB = '''\
from __future__ import annotations

from typing import Any


class {class_name}:
    """Custom validation rule."""

    def passes(self, field: str, value: Any) -> bool:
        return True

    def message(self) -> str:
        return "The :field field is invalid."
'''


@click.command("make:rule")
@click.argument("name")
def make_rule_command(name: str) -> None:
    """Create a new custom validation rule class."""
    from hunt.console.commands.make import load_stub

    class_name = Str.pascal(Str.snake(name))
    content = load_stub("rule", _STUB).format(class_name=class_name)

    out = Path.cwd() / "app" / "rules" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(content)
    click.echo(f"  Created Rule: {out.relative_to(Path.cwd())}")
