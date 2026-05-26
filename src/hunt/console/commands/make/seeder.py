from __future__ import annotations

from pathlib import Path

import click

_STUB = """\
from __future__ import annotations

from hunt.database.seeder import Seeder


class {class_name}(Seeder):
    def run(self) -> None:
        pass
"""


@click.command("make:seeder")
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_seeder_command(name: str, dry_run: bool, as_json: bool) -> None:
    """Create a new database Seeder class."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)
    _create_seeder(name)
    output.finish()


def _create_seeder(name: str) -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._output import output

    class_name = name if name.endswith("Seeder") else f"{name}Seeder"
    out = Path.cwd() / "database" / "seeders" / f"{class_name}.py"
    content = load_stub("seeder", _STUB).format(class_name=class_name)
    output.write(out, content, label="Created Seeder    ")
