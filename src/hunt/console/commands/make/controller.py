from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:controller")
@click.argument("name")
@click.option("--resource", is_flag=True, help="Generate a resource controller with CRUD methods")
@click.option("--api", is_flag=True, help="Generate an API resource controller (no create/edit views)")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_controller_command(name: str, resource: bool, api: bool, dry_run: bool, as_json: bool) -> None:
    """Create a new controller class."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)
    _create_controller(name, resource=resource or api, api=api)
    output.finish()


def _create_controller(name: str, resource: bool = False, api: bool = False) -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._output import output

    class_name = Str.pascal(name)
    if resource and not api:
        stub = load_stub("controller.resource", _RESOURCE_STUB)
    elif api:
        stub = load_stub("controller.api", _API_STUB)
    else:
        stub = load_stub("controller", _PLAIN_STUB)
    content = stub.replace("{{class}}", class_name)

    out = Path.cwd() / "app" / "controllers" / f"{Str.snake(name)}.py"
    output.write(out, content, label="Created Controller")


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
