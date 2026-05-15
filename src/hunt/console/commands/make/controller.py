from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:controller")
@click.argument("name")
@click.option("--resource", is_flag=True, help="Generate a resource controller with CRUD methods")
@click.option("--api", is_flag=True, help="Generate an API resource controller (no create/edit views)")
def make_controller_command(name: str, resource: bool, api: bool) -> None:
    """Create a new controller class."""
    _create_controller(name, resource=resource or api, api=api)


def _create_controller(name: str, resource: bool = False, api: bool = False) -> None:
    class_name = Str.pascal(name)
    if resource and not api:
        stub = _RESOURCE_STUB
    elif api:
        stub = _API_STUB
    else:
        stub = _PLAIN_STUB
    content = stub.replace("{{class}}", class_name)

    out = Path.cwd() / "app" / "controllers" / f"{Str.snake(name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Controller: {out.relative_to(Path.cwd())}")


_PLAIN_STUB = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response


class {{class}}(Controller):
    def index(self, request: Request) -> Response:
        return self.view("welcome")
"""

_RESOURCE_STUB = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response


class {{class}}(Controller):
    def index(self, request: Request) -> Response:
        return self.view("index")

    def create(self, request: Request) -> Response:
        return self.view("create")

    def store(self, request: Request) -> Response:
        return self.redirect("/")

    def show(self, request: Request, id: str) -> Response:
        return self.view("show")

    def edit(self, request: Request, id: str) -> Response:
        return self.view("edit")

    def update(self, request: Request, id: str) -> Response:
        return self.redirect("/")

    def destroy(self, request: Request, id: str) -> Response:
        return self.redirect("/")
"""

_API_STUB = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import JsonResponse


class {{class}}(Controller):
    def index(self, request: Request) -> JsonResponse:
        return self.json([])

    def store(self, request: Request) -> JsonResponse:
        return self.json({}, 201)

    def show(self, request: Request, id: str) -> JsonResponse:
        return self.json({})

    def update(self, request: Request, id: str) -> JsonResponse:
        return self.json({})

    def destroy(self, request: Request, id: str) -> JsonResponse:
        return self.json(None, 204)
"""
