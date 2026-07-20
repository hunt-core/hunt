from __future__ import annotations

from pathlib import Path

import click

_STUB = """\
from __future__ import annotations

from hunt.events.dispatcher import Event


class {class_name}(Event):
    def __init__(self) -> None:
        pass
"""


@click.command("make:event")
@click.argument("name")
def make_event_command(name: str) -> None:
    """Create a new Event class."""
    from hunt.console.commands.make import load_stub

    out = Path.cwd() / "app" / "events" / f"{name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(load_stub("event", _STUB).format(class_name=name), encoding="utf-8")
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
