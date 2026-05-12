from __future__ import annotations

from pathlib import Path

import click


_STUB = '''\
from __future__ import annotations

from hunt.queue.job import Job


class {class_name}(Job):
    queue = "default"

    def __init__(self) -> None:
        pass

    def handle(self) -> None:
        pass

    def failed(self, exc: Exception) -> None:
        pass
'''


@click.command("make:job")
@click.argument("name")
def make_job_command(name: str) -> None:
    """Create a new Job class."""
    out = Path.cwd() / "app" / "jobs" / f"{name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(_STUB.format(class_name=name))
    click.echo(f"  Created: {out.relative_to(Path.cwd())}")
