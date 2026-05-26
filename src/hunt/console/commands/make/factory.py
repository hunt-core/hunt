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
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_factory_command(name: str, model_name: str, dry_run: bool, as_json: bool) -> None:
    """Create a new model Factory class."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)
    _create_factory(name, model_name=model_name)
    output.finish()


def _create_factory(name: str, model_name: str = "") -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._output import output

    class_name = name if name.endswith("Factory") else f"{name}Factory"
    if not model_name:
        model_name = name.replace("Factory", "")
    out = Path.cwd() / "database" / "factories" / f"{class_name}.py"
    content = load_stub("factory", _STUB).format(class_name=class_name, model_name=model_name)
    output.write(out, content, label="Created Factory   ")
