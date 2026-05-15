from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str

_STUB = '''\
from __future__ import annotations

import click


@click.command("{command_name}")
def {func_name}() -> None:
    """{class_name}."""
    pass
'''


@click.command("make:command")
@click.argument("name")
@click.option("--command", "command_name", default=None, help="The CLI command signature (e.g. 'send:emails')")
def make_command_command(name: str, command_name: str | None) -> None:
    """Create a new console command."""
    class_name = Str.pascal(Str.snake(name))
    snake = Str.snake(name)
    func_name = (snake[: -len("_command")] if snake.endswith("_command") else snake) + "_command"
    cmd = command_name or Str.snake(name).replace("_", "-")

    content = _STUB.format(
        class_name=class_name,
        func_name=func_name,
        command_name=cmd,
    )

    out = Path.cwd() / "app" / "console" / "commands" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(content)
    click.echo(f"  Created Command: {out.relative_to(Path.cwd())}")
