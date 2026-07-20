from __future__ import annotations

import time
from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:api")
@click.argument("name")
@click.option("--fields", default="", metavar="FIELDS", help='Column definitions, e.g. "title:string body:text"')
def make_api_command(name: str, fields: str) -> None:
    """Scaffold an API resource: model, migration, API controller, resource class, and routes."""
    from hunt.console.commands.make.field_types import parse_fields

    parsed = parse_fields(fields)
    class_name = Str.pascal(name)
    snake = Str.snake(name)
    table = Str.plural(snake)
    route_prefix = Str.slug(table, "-")

    _make_model(class_name, table, parsed)
    _make_migration(class_name, table, parsed)
    _make_resource_class(class_name, snake, parsed)
    _make_controller(class_name, snake, route_prefix, parsed)
    _append_routes(class_name, snake, route_prefix)


# ---------------------------------------------------------------------------


def _make_model(class_name: str, table: str, fields: list[tuple[str, str]]) -> None:
    from hunt.console.commands.make.field_types import fillable_list

    fill = fillable_list(fields) if fields else "[]"
    content = _MODEL_STUB.replace("{{class}}", class_name).replace("{{table}}", table).replace("{{fillable}}", fill)
    out = Path.cwd() / "app" / "models" / f"{Str.snake(class_name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    click.echo(f"  Created Model:      {out.relative_to(Path.cwd())}")


def _make_migration(class_name: str, table: str, fields: list[tuple[str, str]]) -> None:
    from hunt.console.commands.make.field_types import migration_columns

    timestamp = time.strftime("%Y_%m_%d_%H%M%S")
    mig_class = Str.pascal(f"create_{table}_table")
    col_lines = migration_columns(fields) if fields else ""
    body = f"\n{col_lines}\n" if col_lines else ""
    content = _MIGRATION_STUB.replace("{{class}}", mig_class).replace("{{table}}", table).replace("{{columns}}", body)
    filename = f"{timestamp}_create_{table}_table"
    out = Path.cwd() / "database" / "migrations" / f"{filename}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    click.echo(f"  Created Migration:  {out.relative_to(Path.cwd())}")


def _make_resource_class(class_name: str, snake: str, fields: list[tuple[str, str]]) -> None:
    col_lines = _resource_to_array(fields)
    content = (
        _RESOURCE_STUB.replace("{{class}}", f"{class_name}Resource")
        .replace("{{model_class}}", class_name)
        .replace("{{model_snake}}", snake)
        .replace("{{model_import}}", f"app.models.{snake}")
        .replace("{{to_array_fields}}", col_lines)
    )
    out = Path.cwd() / "app" / "resources" / f"{snake}_resource.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    click.echo(f"  Created Resource:   {out.relative_to(Path.cwd())}")


def _make_controller(class_name: str, snake: str, route_prefix: str, fields: list[tuple[str, str]]) -> None:
    col_names = [col for col, _ in fields]
    store_lines = _store_lines(col_names, class_name)
    update_lines = _update_lines(col_names)
    content = (
        _CONTROLLER_STUB.replace("{{class}}", f"{class_name}Controller")
        .replace("{{model_class}}", class_name)
        .replace("{{model_snake}}", snake)
        .replace("{{model_import}}", f"app.models.{snake}")
        .replace("{{resource_class}}", f"{class_name}Resource")
        .replace("{{resource_import}}", f"app.resources.{snake}_resource")
        .replace("{{route_prefix}}", route_prefix)
        .replace("{{store_lines}}", store_lines)
        .replace("{{update_lines}}", update_lines)
    )
    out = Path.cwd() / "app" / "controllers" / f"{snake}_controller.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    click.echo(f"  Created Controller: {out.relative_to(Path.cwd())}")


def _append_routes(class_name: str, snake: str, route_prefix: str) -> None:
    routes_file = Path.cwd() / "routes" / "api.py"
    if not routes_file.exists():
        click.echo("  Warning: routes/api.py not found — routes not appended.", err=True)
        return

    block = (
        _ROUTES_BLOCK.replace("{{class}}", class_name).replace("{{snake}}", snake).replace("{{prefix}}", route_prefix)
    )
    existing = routes_file.read_text()
    if f"/{route_prefix}" in existing:
        click.echo(f"  Skipped Routes:     /api/{route_prefix} already in routes/api.py")
        return
    routes_file.write_text(existing.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    click.echo(f"  Updated Routes:     routes/api.py  (/api/{route_prefix})")


# ---------------------------------------------------------------------------
# helpers


def _resource_to_array(fields: list[tuple[str, str]]) -> str:
    if not fields:
        return '            "id": self.model.id,'
    lines = ['            "id": self.model.id,']
    for col, _ in fields:
        lines.append(f'            "{col}": self.model.{col},')
    return "\n".join(lines)


def _store_lines(cols: list[str], class_name: str) -> str:
    if not cols:
        return f"        item = {class_name}.create({{}})\n"
    assignments = ", ".join(f'"{c}": request.input("{c}")' for c in cols)
    return f"        item = {class_name}.create({{{assignments}}})\n"


def _update_lines(cols: list[str]) -> str:
    if not cols:
        return ""
    lines = [f'        item.{c} = request.input("{c}")' for c in cols]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# stubs

_MODEL_STUB = """\
from hunt.database.model import Model


class {{class}}(Model):
    table = "{{table}}"
    fillable: list[str] = {{fillable}}
    hidden: list[str] = []
"""

_MIGRATION_STUB = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.blueprint import Blueprint
from hunt.database.schema.migration import Migration


class {{class}}(Migration):
    def up(self) -> None:
        Schema.create("{{table}}", lambda bp: [
            bp.id(),{{columns}}
            bp.timestamps(),
        ])

    def down(self) -> None:
        Schema.drop_if_exists("{{table}}")
"""

_RESOURCE_STUB = """\
from __future__ import annotations

from {{model_import}} import {{model_class}}


class {{class}}:
    def __init__(self, model: {{model_class}}) -> None:
        self.model = model

    def to_array(self) -> dict:
        return {
{{to_array_fields}}
        }

    @classmethod
    def collection(cls, items) -> list[dict]:
        return [cls(m).to_array() for m in items]
"""

_CONTROLLER_STUB = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import JsonResponse
from {{model_import}} import {{model_class}}
from {{resource_import}} import {{resource_class}}


class {{class}}(Controller):
    def index(self, request: Request) -> JsonResponse:
        items = {{model_class}}.all()
        return self.json({"data": {{resource_class}}.collection(items)})

    def store(self, request: Request) -> JsonResponse:
{{store_lines}}        return self.json({"data": {{resource_class}}(item).to_array()}, 201)

    def show(self, request: Request, id: str) -> JsonResponse:
        item = {{model_class}}.find_or_fail(int(id))
        return self.json({"data": {{resource_class}}(item).to_array()})

    def update(self, request: Request, id: str) -> JsonResponse:
        item = {{model_class}}.find_or_fail(int(id))
{{update_lines}}        item.save()
        return self.json({"data": {{resource_class}}(item).to_array()})

    def destroy(self, request: Request, id: str) -> JsonResponse:
        item = {{model_class}}.find_or_fail(int(id))
        item.delete()
        return self.json(None, 204)
"""

_ROUTES_BLOCK = """\
    # {{class}} API resource
    from app.controllers.{{snake}}_controller import {{class}}Controller
    _c = {{class}}Controller()
    router.get("/api/{{prefix}}", _c.index).named("api.{{prefix}}.index")
    router.post("/api/{{prefix}}", _c.store).named("api.{{prefix}}.store")
    router.get("/api/{{prefix}}/{id}", _c.show).named("api.{{prefix}}.show")
    router.put("/api/{{prefix}}/{id}", _c.update).named("api.{{prefix}}.update")
    router.delete("/api/{{prefix}}/{id}", _c.destroy).named("api.{{prefix}}.destroy")\
"""
