from __future__ import annotations

from pathlib import Path

import click


_STUB = '''\
from __future__ import annotations

from hunt.database.seeder import Seeder


class {class_name}(Seeder):
    def run(self) -> None:
        pass
'''


@click.command("make:seeder")
@click.argument("name")
def make_seeder_command(name: str) -> None:
    """Create a new database Seeder class."""
    class_name = name if name.endswith("Seeder") else f"{name}Seeder"
    out = Path.cwd() / "database" / "seeders" / f"{class_name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(_STUB.format(class_name=class_name))
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
