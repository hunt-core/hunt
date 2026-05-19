from __future__ import annotations

from pathlib import Path

import click

_STUB = """\
from __future__ import annotations

from typing import Any


class {class_name}:
    def handle(self, event: Any) -> None:
        pass
"""

_QUEUED_STUB = """\
from __future__ import annotations

from typing import Any

from hunt.queue.job import Job


class {class_name}(Job):
    implements_queued_listener = True

    def handle(self, event: Any = None) -> None:
        pass
"""


@click.command("make:listener")
@click.argument("name")
@click.option("--event", "event_name", default="", help="Event class this listener handles")
@click.option("-q", "--queued", is_flag=True, help="Make this a queued (Job-based) listener")
def make_listener_command(name: str, event_name: str, queued: bool) -> None:
    """Create a new Listener class."""
    from hunt.console.commands.make import load_stub

    stub_name = "listener.queued" if queued else "listener"
    stub = load_stub(stub_name, _QUEUED_STUB if queued else _STUB)
    out = Path.cwd() / "app" / "listeners" / f"{name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(stub.format(class_name=name))
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
    click.echo("  Register it in app/providers/event_service_provider.py")
