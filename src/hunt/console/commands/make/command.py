from __future__ import annotations

import ast
import re
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
    from hunt.console.commands.make._naming import validate_command_name, validate_identifier

    validate_identifier(name, "command name")
    class_name = Str.pascal(Str.snake(name))
    snake = Str.snake(name)
    func_name = (snake[: -len("_command")] if snake.endswith("_command") else snake) + "_command"
    cmd = validate_command_name(command_name or Str.snake(name).replace("_", "-"))

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
    out.write_text(content, encoding="utf-8")
    click.echo(f"  Created Command: {out.relative_to(Path.cwd())}")

    # Auto-register the new command in app/console/kernel.py
    kernel_file = Path.cwd() / "app" / "console" / "kernel.py"
    if not kernel_file.exists():
        return
    import_line = f"from app.console.commands.{snake_name} import {func_name}"
    patched = _register_in_kernel(kernel_file.read_text(encoding="utf-8"), import_line, func_name)
    if patched is None:
        click.echo(
            "  Could not register the command automatically. Add these lines to app/console/kernel.py:\n"
            f"      {import_line}\n"
            f"      cli.add_command({func_name})   # inside register()",
            err=True,
        )
        return
    kernel_file.write_text(patched, encoding="utf-8")
    click.echo("  Registered in: app/console/kernel.py")


_ADD_COMMAND_RE = re.compile(r"^(\s+)cli\.add_command\(")


def _register_in_kernel(src: str, import_line: str, func_name: str) -> str | None:
    """Return *src* with the import and ``cli.add_command(...)`` call added.

    The call goes after the last existing ``cli.add_command(`` line of
    ``register()``; the first command replaces the scaffold's ``pass``
    placeholder. Returns ``None`` when the result cannot be verified, so the
    caller never reports a registration that did not happen.
    """
    register_call = f"cli.add_command({func_name})"
    lines = src.splitlines()

    def_idx = next((i for i, line in enumerate(lines) if line.startswith("def register(")), None)
    if def_idx is None:
        return None

    # Body of register(): up to the next top-level statement.
    end = len(lines)
    for i in range(def_idx + 1, len(lines)):
        if lines[i].strip() and not lines[i][0].isspace():
            end = i
            break

    if not any(line.strip() == register_call for line in lines[def_idx:end]):
        last_call = None
        pass_idx = None
        last_code = def_idx
        for i in range(def_idx + 1, end):
            if _ADD_COMMAND_RE.match(lines[i]):
                last_call = i
            if pass_idx is None and lines[i].strip() == "pass":
                pass_idx = i
            if lines[i].strip():
                last_code = i
        if last_call is not None:
            indent = _ADD_COMMAND_RE.match(lines[last_call]).group(1)  # type: ignore[union-attr]
            lines.insert(last_call + 1, f"{indent}{register_call}")
        elif pass_idx is not None:
            indent = lines[pass_idx][: len(lines[pass_idx]) - len(lines[pass_idx].lstrip())]
            lines[pass_idx] = f"{indent}{register_call}"
        else:
            lines.insert(last_code + 1, f"    {register_call}")

    if import_line not in lines:
        last_import = max((i for i in range(def_idx) if lines[i].startswith(("from ", "import "))), default=None)
        if last_import is not None:
            lines.insert(last_import + 1, import_line)
        else:
            lines[def_idx:def_idx] = [import_line, "", ""]

    patched = "\n".join(lines) + "\n"
    try:
        tree = ast.parse(patched)
    except SyntaxError:
        return None
    register_fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "register"), None)
    if register_fn is None:
        return None
    registered = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_command"
        and any(isinstance(arg, ast.Name) and arg.id == func_name for arg in node.args)
        for node in ast.walk(register_fn)
    )
    imported = any(
        isinstance(node, ast.ImportFrom) and any(alias.name == func_name for alias in node.names) for node in tree.body
    )
    return patched if registered and imported else None
