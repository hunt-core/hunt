from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str

_STUB = """\
from __future__ import annotations


class {class_name}:
    def view_any(self, user) -> bool:
        return True

    def view(self, user, model) -> bool:
        return True

    def create(self, user) -> bool:
        return True

    def update(self, user, model) -> bool:
        return True

    def delete(self, user, model) -> bool:
        return True

    def restore(self, user, model) -> bool:
        return True

    def force_delete(self, user, model) -> bool:
        return True
"""


@click.command("make:policy")
@click.argument("name")
@click.option("-m", "--model", default=None, help="The model the policy applies to")
def make_policy_command(name: str, model: str | None) -> None:
    """Create a new policy class."""
    class_name = Str.pascal(Str.snake(name))
    content = _STUB.format(class_name=class_name)

    if model:
        model_class = Str.pascal(Str.snake(model))
        model_import = f"from app.models.{Str.snake(model)} import {model_class}\n"
        content = content.replace(
            "from __future__ import annotations\n",
            f"from __future__ import annotations\n\n{model_import}",
        )

    out = Path.cwd() / "app" / "policies" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(content)
    click.echo(f"  Created Policy: {out.relative_to(Path.cwd())}")
