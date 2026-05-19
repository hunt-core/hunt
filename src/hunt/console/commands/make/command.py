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
    from hunt.console.commands.make import load_stub

    class_name = Str.pascal(Str.snake(name))
    snake = Str.snake(name)
    func_name = (snake[: -len("_command")] if snake.endswith("_command") else snake) + "_command"
    cmd = command_name or Str.snake(name).replace("_", "-")

    content = load_stub("command", _STUB).format(
        class_name=class_name,
        func_name=func_name,
        command_name=cmd,
    )

    snake_name = Str.snake(name)
    out = Path.cwd() / "app" / "console" / "commands" / f"{snake_name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(content)
    click.echo(f"  Created Command: {out.relative_to(Path.cwd())}")

    # Auto-register the new command in app/console/kernel.py
    kernel_file = Path.cwd() / "app" / "console" / "kernel.py"
    if kernel_file.exists():
        module_path = f"app.console.commands.{snake_name}"
        import_line = f"from {module_path} import {func_name}"
        register_line = f"    cli.add_command({func_name})"
        src = kernel_file.read_text()
        if func_name not in src:
            # Insert import before the register function, add command inside it
            src = src.replace(
                "\ndef register(",
                f"\n{import_line}\n\ndef register(",
            )
            src = src.replace(
                "    pass\n",
                f"    {func_name},\n    pass\n",
                1,
            )
            # Replace "pass" placeholder with the real add_command call
            src = src.replace(
                f"    {func_name},\n    pass\n",
                f"{register_line}\n",
                1,
            )
            kernel_file.write_text(src)
            click.echo("  Registered in: app/console/kernel.py")
