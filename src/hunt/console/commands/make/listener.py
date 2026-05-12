from __future__ import annotations

from pathlib import Path

import click


_STUB = '''\
from __future__ import annotations

from typing import Any


class {class_name}:
    def handle(self, event: Any) -> None:
        pass
'''


@click.command("make:listener")
@click.argument("name")
@click.option("--event", "event_name", default="", help="Event class this listener handles")
def make_listener_command(name: str, event_name: str) -> None:
    """Create a new Listener class."""
    out = Path.cwd() / "app" / "listeners" / f"{name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(_STUB.format(class_name=name))
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
