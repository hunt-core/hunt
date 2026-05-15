from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:middleware")
@click.argument("name")
def make_middleware_command(name: str) -> None:
    """Create a new middleware class."""
    class_name = Str.pascal(name)
    content = _STUB.replace("{{class}}", class_name)

    out = Path.cwd() / "app" / "middleware" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Middleware: {out.relative_to(Path.cwd())}")


_STUB = """\
from hunt.http.middleware import Middleware
from hunt.http.request import Request
from hunt.http.response import Response
from hunt.http.middleware import Next


class {{class}}(Middleware):
    async def handle(self, request: Request, next: Next) -> Response:
        return await next(request)
"""
