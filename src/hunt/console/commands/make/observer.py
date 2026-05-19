from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str

_STUB = """\
from __future__ import annotations


class {class_name}:
    def creating(self, model) -> None:
        pass

    def created(self, model) -> None:
        pass

    def updating(self, model) -> None:
        pass

    def updated(self, model) -> None:
        pass

    def saving(self, model) -> None:
        pass

    def saved(self, model) -> None:
        pass

    def deleting(self, model) -> None:
        pass

    def deleted(self, model) -> None:
        pass

    def restoring(self, model) -> None:
        pass

    def restored(self, model) -> None:
        pass
"""


@click.command("make:observer")
@click.argument("name")
@click.option("-m", "--model", default=None, help="The model the observer watches")
def make_observer_command(name: str, model: str | None) -> None:
    """Create a new model observer class."""
    from hunt.console.commands.make import load_stub

    class_name = Str.pascal(Str.snake(name))
    content = load_stub("observer", _STUB).format(class_name=class_name)

    if model:
        model_class = Str.pascal(Str.snake(model))
        model_import = f"from app.models.{Str.snake(model)} import {model_class}\n"
        content = content.replace(
            "from __future__ import annotations\n",
            f"from __future__ import annotations\n\n{model_import}",
        )
        # Update type hints to use the real model class
        content = content.replace(", model)", f", model: {model_class})")

    out = Path.cwd() / "app" / "observers" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(content)
    click.echo(f"  Created Observer: {out.relative_to(Path.cwd())}")
