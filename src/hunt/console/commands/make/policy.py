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
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_policy_command(name: str, model: str | None, dry_run: bool, as_json: bool) -> None:
    """Create a new policy class."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)
    _create_policy(name, model=model)
    output.finish()


def _create_policy(name: str, model: str | None = None) -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._output import output

    class_name = Str.pascal(Str.snake(name))
    content = load_stub("policy", _STUB).format(class_name=class_name)

    if model:
        model_class = Str.pascal(Str.snake(model))
        model_import = f"from app.models.{Str.snake(model)} import {model_class}\n"
        content = content.replace(
            "from __future__ import annotations\n",
            f"from __future__ import annotations\n\n{model_import}",
        )

    out = Path.cwd() / "app" / "policies" / f"{Str.snake(name)}.py"
    output.write(out, content, label="Created Policy    ")
