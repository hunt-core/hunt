from __future__ import annotations

import time
from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:crud")
@click.argument("name")
@click.option("--fields", default="", metavar="FIELDS", help='Column definitions, e.g. "title:string body:text"')
def make_crud_command(name: str, fields: str) -> None:
    """Scaffold a full CRUD resource: model, migration, controller, views, and routes."""
    from hunt.console.commands.make.field_types import parse_fields

    parsed = parse_fields(fields)
    class_name = Str.pascal(name)
    snake = Str.snake(name)
    table = Str.plural(snake)
    route_prefix = Str.slug(table, "-")
    view_dir = route_prefix

    _make_model(class_name, table, parsed)
    _make_migration(class_name, table, parsed)
    _make_controller(class_name, snake, table, route_prefix, view_dir, parsed)
    _make_views(view_dir, class_name, route_prefix, parsed)
    _append_routes(class_name, snake, route_prefix)


# ---------------------------------------------------------------------------

def _make_model(class_name: str, table: str, fields: list[tuple[str, str]]) -> None:
    from hunt.console.commands.make.field_types import fillable_list

    fill = fillable_list(fields) if fields else "[]"
    content = _MODEL_STUB.replace("{{class}}", class_name).replace("{{table}}", table).replace("{{fillable}}", fill)
    out = Path.cwd() / "app" / "models" / f"{Str.snake(class_name)}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Model:      {out.relative_to(Path.cwd())}")


def _make_migration(class_name: str, table: str, fields: list[tuple[str, str]]) -> None:
    from hunt.console.commands.make.field_types import migration_columns

    timestamp = time.strftime("%Y_%m_%d_%H%M%S")
    mig_class = Str.pascal(f"create_{table}_table")
    col_lines = migration_columns(fields) if fields else ""
    body = (f"\n{col_lines}\n" if col_lines else "")
    content = _MIGRATION_STUB.replace("{{class}}", mig_class).replace("{{table}}", table).replace("{{columns}}", body)
    filename = f"{timestamp}_create_{table}_table"
    out = Path.cwd() / "database" / "migrations" / f"{filename}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Migration:  {out.relative_to(Path.cwd())}")


def _make_controller(
    class_name: str, snake: str, table: str, route_prefix: str,
    view_dir: str, fields: list[tuple[str, str]],
) -> None:
    col_names = [col for col, _ in fields]
    store_lines = _store_lines(col_names, class_name)
    update_lines = _update_lines(col_names)
    content = (
        _CONTROLLER_STUB
        .replace("{{class}}", f"{class_name}Controller")
        .replace("{{model_class}}", class_name)
        .replace("{{model_snake}}", snake)
        .replace("{{model_import}}", f"app.models.{snake}")
        .replace("{{view_dir}}", view_dir)
        .replace("{{route_prefix}}", route_prefix)
        .replace("{{store_lines}}", store_lines)
        .replace("{{update_lines}}", update_lines)
    )
    out = Path.cwd() / "app" / "controllers" / f"{snake}_controller.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Controller: {out.relative_to(Path.cwd())}")


def _make_views(view_dir: str, class_name: str, route_prefix: str, fields: list[tuple[str, str]]) -> None:
    base = Path.cwd() / "resources" / "views" / view_dir
    base.mkdir(parents=True, exist_ok=True)

    col_names = [col for col, _ in fields]
    form_fields_html = _form_fields_html(col_names)
    table_headers = "".join(f"                <th>{col.replace('_', ' ').title()}</th>\n" for col in col_names)
    table_cells = "".join(f"                    <td>{{{{ item.{col} }}}}</td>\n" for col in col_names)

    views = {
        "index.html": _VIEW_INDEX.replace("{{class}}", class_name).replace("{{route_prefix}}", route_prefix)
                                 .replace("{{table_headers}}", table_headers).replace("{{table_cells}}", table_cells),
        "create.html": _VIEW_CREATE.replace("{{class}}", class_name).replace("{{route_prefix}}", route_prefix)
                                    .replace("{{form_fields}}", form_fields_html),
        "edit.html": _VIEW_EDIT.replace("{{class}}", class_name).replace("{{route_prefix}}", route_prefix)
                                 .replace("{{form_fields}}", form_fields_html),
        "show.html": _VIEW_SHOW.replace("{{class}}", class_name).replace("{{route_prefix}}", route_prefix)
                                 .replace("{{field_rows}}", _show_fields_html(col_names)),
    }
    for filename, content in views.items():
        f = base / filename
        f.write_text(content)
        click.echo(f"  Created View:       resources/views/{view_dir}/{filename}")


def _append_routes(class_name: str, snake: str, route_prefix: str) -> None:
    routes_file = Path.cwd() / "routes" / "web.py"
    if not routes_file.exists():
        click.echo("  Warning: routes/web.py not found — routes not appended.", err=True)
        return

    block = _ROUTES_BLOCK.replace("{{class}}", class_name).replace("{{snake}}", snake).replace("{{prefix}}", route_prefix)
    existing = routes_file.read_text()
    if f"/{route_prefix}" in existing:
        click.echo(f"  Skipped Routes:     /{route_prefix} already in routes/web.py")
        return
    routes_file.write_text(existing.rstrip() + "\n\n" + block + "\n")
    click.echo(f"  Updated Routes:     routes/web.py  (/{route_prefix})")


# ---------------------------------------------------------------------------
# helpers

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


def _form_fields_html(cols: list[str]) -> str:
    parts = []
    for col in cols:
        label = col.replace("_", " ").title()
        parts.append(
            f'            <div class="mb-4">\n'
            f'                <label class="block text-sm font-medium text-gray-700 mb-1">{label}</label>\n'
            f'                <input type="text" name="{col}" value="{{{{ old(\'{col}\') }}}}" '
            f'class="w-full border border-gray-300 rounded px-3 py-2">\n'
            f'                @error(\'{col}\')<p class="text-red-600 text-sm mt-1">{{{{ message }}}}</p>@enderror\n'
            f'            </div>'
        )
    return "\n".join(parts)


def _show_fields_html(cols: list[str]) -> str:
    parts = []
    for col in cols:
        label = col.replace("_", " ").title()
        parts.append(
            f'            <div class="py-2 border-b">\n'
            f'                <span class="font-medium text-gray-600">{label}:</span>\n'
            f'                <span class="ml-2">{{{{ item.{col} }}}}</span>\n'
            f'            </div>'
        )
    return "\n".join(parts)


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

_CONTROLLER_STUB = """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response
from {{model_import}} import {{model_class}}


class {{class}}(Controller):
    def index(self, request: Request) -> Response:
        items = {{model_class}}.all()
        return self.view("{{view_dir}}/index", {"items": items})

    def create(self, request: Request) -> Response:
        return self.view("{{view_dir}}/create")

    def store(self, request: Request) -> Response:
{{store_lines}}        return self.redirect("/{{route_prefix}}")

    def show(self, request: Request, id: str) -> Response:
        item = {{model_class}}.find_or_fail(int(id))
        return self.view("{{view_dir}}/show", {"item": item})

    def edit(self, request: Request, id: str) -> Response:
        item = {{model_class}}.find_or_fail(int(id))
        return self.view("{{view_dir}}/edit", {"item": item})

    def update(self, request: Request, id: str) -> Response:
        item = {{model_class}}.find_or_fail(int(id))
{{update_lines}}        item.save()
        return self.redirect("/{{route_prefix}}")

    def destroy(self, request: Request, id: str) -> Response:
        item = {{model_class}}.find_or_fail(int(id))
        item.delete()
        return self.redirect("/{{route_prefix}}")
"""

_ROUTES_BLOCK = """\
    # {{class}} CRUD
    from app.controllers.{{snake}}_controller import {{class}}Controller
    _c = {{class}}Controller()
    router.get("/{{prefix}}", _c.index).named("{{prefix}}.index")
    router.get("/{{prefix}}/create", _c.create).named("{{prefix}}.create")
    router.post("/{{prefix}}", _c.store).named("{{prefix}}.store")
    router.get("/{{prefix}}/{id}", _c.show).named("{{prefix}}.show")
    router.get("/{{prefix}}/{id}/edit", _c.edit).named("{{prefix}}.edit")
    router.put("/{{prefix}}/{id}", _c.update).named("{{prefix}}.update")
    router.delete("/{{prefix}}/{id}", _c.destroy).named("{{prefix}}.destroy")\
"""

_VIEW_INDEX = """\
@extends('layouts.app')

@section('content')
<div class="max-w-4xl mx-auto py-8">
    <div class="flex justify-between items-center mb-6">
        <h1 class="text-2xl font-bold">{{class}}</h1>
        <a href="/{{route_prefix}}/create" class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">New</a>
    </div>
    <table class="w-full border-collapse bg-white shadow rounded">
        <thead class="bg-gray-50">
            <tr>
                <th class="text-left px-4 py-2">ID</th>
{{table_headers}}                <th class="text-left px-4 py-2">Actions</th>
            </tr>
        </thead>
        <tbody>
            @foreach($items as $item)
            <tr class="border-t hover:bg-gray-50">
                <td class="px-4 py-2">{{ item.id }}</td>
{{table_cells}}                <td class="px-4 py-2 space-x-2">
                    <a href="/{{route_prefix}}/{{ item.id }}" class="text-blue-600 hover:underline">View</a>
                    <a href="/{{route_prefix}}/{{ item.id }}/edit" class="text-yellow-600 hover:underline">Edit</a>
                    <form method="POST" action="/{{route_prefix}}/{{ item.id }}" style="display:inline">
                        @csrf @method('DELETE')
                        <button type="submit" class="text-red-600 hover:underline"
                            onclick="return confirm('Delete?')">Delete</button>
                    </form>
                </td>
            </tr>
            @endforeach
        </tbody>
    </table>
</div>
@endsection
"""

_VIEW_SHOW = """\
@extends('layouts.app')

@section('content')
<div class="max-w-2xl mx-auto py-8">
    <h1 class="text-2xl font-bold mb-6">{{class}} #{{ item.id }}</h1>
    <div class="bg-white shadow rounded p-6">
{{field_rows}}
    </div>
    <div class="mt-4 space-x-2">
        <a href="/{{route_prefix}}/{{ item.id }}/edit" class="bg-yellow-500 text-white px-4 py-2 rounded hover:bg-yellow-600">Edit</a>
        <a href="/{{route_prefix}}" class="text-gray-600 hover:underline">Back</a>
    </div>
</div>
@endsection
"""

_VIEW_CREATE = """\
@extends('layouts.app')

@section('content')
<div class="max-w-2xl mx-auto py-8">
    <h1 class="text-2xl font-bold mb-6">New {{class}}</h1>
    <form method="POST" action="/{{route_prefix}}" class="bg-white shadow rounded p-6 space-y-4">
        @csrf
        @errors
{{form_fields}}
        <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">Create</button>
        <a href="/{{route_prefix}}" class="ml-2 text-gray-600 hover:underline">Cancel</a>
    </form>
</div>
@endsection
"""

_VIEW_EDIT = """\
@extends('layouts.app')

@section('content')
<div class="max-w-2xl mx-auto py-8">
    <h1 class="text-2xl font-bold mb-6">Edit {{class}} #{{ item.id }}</h1>
    <form method="POST" action="/{{route_prefix}}/{{ item.id }}" class="bg-white shadow rounded p-6 space-y-4">
        @csrf @method('PUT')
        @errors
{{form_fields}}
        <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">Update</button>
        <a href="/{{route_prefix}}" class="ml-2 text-gray-600 hover:underline">Cancel</a>
    </form>
</div>
@endsection
"""
