from __future__ import annotations

from pathlib import Path

import click

_STUB = """\
from __future__ import annotations

from hunt.database.factory import Factory


class {class_name}(Factory):
    # model = {model_name}

    def definition(self) -> dict:
        return {{
            # "name": self.random_string(),
        }}
"""


@click.command("make:factory")
@click.argument("name")
@click.option("--model", "model_name", default="", help="Model class this factory creates")
def make_factory_command(name: str, model_name: str) -> None:
    """Create a new model Factory class."""
    from hunt.console.commands.make import load_stub

    class_name = name if name.endswith("Factory") else f"{name}Factory"
    if not model_name:
        model_name = name.replace("Factory", "")
    out = Path.cwd() / "database" / "factories" / f"{class_name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(load_stub("factory", _STUB).format(class_name=class_name, model_name=model_name))
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
