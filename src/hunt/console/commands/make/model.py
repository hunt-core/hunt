from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:model")
@click.argument("name")
@click.option("-m", "--migration", is_flag=True, help="Create a migration for the model")
@click.option("-c", "--controller", is_flag=True, help="Create a controller for the model")
def make_model_command(name: str, migration: bool, controller: bool) -> None:
    """Create a new model class."""
    class_name = Str.pascal(name)
    table = Str.plural(Str.snake(name))
    stub = _load_stub("model")
    content = stub.replace("{{class}}", class_name).replace("{{table}}", table)

    out = Path.cwd() / "app" / "models" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Model: {out.relative_to(Path.cwd())}")

    if migration:
        from hunt.console.commands.make.migration import _create_migration

        _create_migration(f"create_{table}_table", table)

    if controller:
        from hunt.console.commands.make.controller import _create_controller

        _create_controller(f"{class_name}Controller", resource=False)


_MODEL_STUB = """\
from hunt.database.model import Model


class {{class}}(Model):
    table = "{{table}}"
    fillable: list[str] = []
    hidden: list[str] = []
"""


def _load_stub(name: str) -> str:
    from hunt.console.commands.make import load_stub

    return load_stub(name, _MODEL_STUB)
