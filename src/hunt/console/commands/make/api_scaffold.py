from __future__ import annotations

from pathlib import Path

import click

from hunt.console.commands.make.crud import _CONTROLLER_HELPERS
from hunt.support.str import Str


@click.command("make:api")
@click.argument("name")
@click.option("--fields", default="", metavar="FIELDS", help='Column definitions, e.g. "title:string body:text"')
@click.option("--public", "public", is_flag=True, help="Do not require authentication on the generated routes")
@click.option("--dry-run", is_flag=True, help="Preview files without writing them")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_api_command(name: str, fields: str, public: bool, dry_run: bool, as_json: bool) -> None:
    """Scaffold an API resource: model, migration, API controller, resource class, and routes."""
    from hunt.console.commands.make._naming import validate_identifier
    from hunt.console.commands.make._output import output
    from hunt.console.commands.make.crud import _make_migration, _make_model
    from hunt.console.commands.make.field_types import parse_fields

    # Validate everything before the first file is written.
    validate_identifier(name, "resource name")
    parsed = parse_fields(fields)

    output.configure(dry_run=dry_run, as_json=as_json)

    class_name = Str.pascal(name)
    snake = Str.snake(name)
    table = Str.plural(snake)
    route_prefix = Str.slug(table, "-")

    # Existing files (e.g. app/models/user.py) are never overwritten.
    _make_model(class_name, table, parsed)
    _make_migration(class_name, table, parsed)
    _make_resource_class(class_name, snake, parsed)
    _make_controller(class_name, snake, route_prefix, parsed)
    _append_routes(class_name, snake, route_prefix, public=public)
    if public:
        output.echo("  Warning: --public given. Anyone can create, change and delete these records.")
    output.finish()


# ---------------------------------------------------------------------------


def _make_resource_class(class_name: str, snake: str, fields: list[tuple[str, str]]) -> None:
    from hunt.console.commands.make._output import output

    col_lines = _resource_to_array(fields)
    content = (
        _RESOURCE_STUB.replace("{{class}}", f"{class_name}Resource")
        .replace("{{model_class}}", class_name)
        .replace("{{model_snake}}", snake)
        .replace("{{model_import}}", f"app.models.{snake}")
        .replace("{{to_array_fields}}", col_lines)
    )
    out = Path.cwd() / "app" / "resources" / f"{snake}_resource.py"
    output.write(out, content, label="Created Resource  ")


def _make_controller(class_name: str, snake: str, route_prefix: str, fields: list[tuple[str, str]]) -> None:
    from hunt.console.commands.make._output import output
    from hunt.console.commands.make.crud import _controller_source

    content = (
        _controller_source(_CONTROLLER_STUB, class_name, snake, fields)
        .replace("{{resource_class}}", f"{class_name}Resource")
        .replace("{{resource_import}}", f"app.resources.{snake}_resource")
        .replace("{{route_prefix}}", route_prefix)
    )
    out = Path.cwd() / "app" / "controllers" / f"{snake}_controller.py"
    output.write(out, content, label="Created Controller")


def _auth_middleware() -> tuple[str, str]:
    """Return (import line, class name) of the middleware that protects generated API routes.

    Apps created with the API starter authenticate with Bearer tokens (ApiAuth);
    everything else falls back to the session-based Authenticate middleware.
    """
    if (Path.cwd() / "app" / "middleware" / "api_auth.py").exists():
        return "from app.middleware.api_auth import ApiAuth", "ApiAuth"
    return "from hunt.http.middleware.authenticate import Authenticate", "Authenticate"


def _append_routes(class_name: str, snake: str, route_prefix: str, public: bool = False) -> None:
    from hunt.console.commands.make._output import output

    routes_file = Path.cwd() / "routes" / "api.py"
    if not routes_file.exists():
        output.echo("  Warning: routes/api.py not found — routes not appended.")
        return

    mw_import, mw_class = _auth_middleware()
    template = _ROUTES_BLOCK_PUBLIC if public else _ROUTES_BLOCK
    block = (
        template.replace("{{class}}", class_name)
        .replace("{{snake}}", snake)
        .replace("{{prefix}}", route_prefix)
        .replace("{{mw_import}}", mw_import)
        .replace("{{mw_class}}", mw_class)
    )
    existing = routes_file.read_text(encoding="utf-8")
    if f'/{route_prefix}"' in existing:
        output.echo(f"  Skipped Routes:     /api/{route_prefix} already in routes/api.py")
        return
    if output.dry_run:
        output.echo(f"  [dry-run] Updated Routes: routes/api.py  (/api/{route_prefix})")
        return
    routes_file.write_text(existing.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
    output.echo(f"  Updated Routes:     routes/api.py  (/api/{route_prefix})")
    if not public:
        output.echo(f"  The routes require authentication ({mw_class}). Pass --public to opt out.")
        if mw_class == "Authenticate":
            output.echo(
                "  Authenticate uses the login session, so write requests also need a CSRF token. "
                "For token clients use Bearer authentication (see the API starter's ApiAuth)."
            )


# ---------------------------------------------------------------------------
# helpers


def _resource_to_array(fields: list[tuple[str, str]]) -> str:
    lines = ['            "id": self.model.id,']
    for col, _ in fields:
        lines.append(f'            "{col}": self.model.{col},')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# stubs

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

_CONTROLLER_STUB = (
    """\
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import HttpException, JsonResponse
from hunt.validation.validator import Validator
from {{model_import}} import {{model_class}}
from {{resource_import}} import {{resource_class}}

"""
    + _CONTROLLER_HELPERS
    + """\


class {{class}}(Controller):
    def index(self, request: Request) -> JsonResponse:
        items = {{model_class}}.all().get()
        return self.json({"data": {{resource_class}}.collection(items)})

    def store(self, request: Request) -> JsonResponse:
        data = _input(request)
        errors = _validate(data, RULES)
        if errors:
            return self.json({"errors": errors}, 422)
        item = {{model_class}}()
        item.fill({**_DEFAULTS, **_clean(data)})
        item.save()
        return self.json({"data": {{resource_class}}(item).to_array()}, 201)

    def show(self, request: Request, id: str) -> JsonResponse:
        return self.json({"data": {{resource_class}}(_find(id)).to_array()})

    def update(self, request: Request, id: str) -> JsonResponse:
        item = _find(id)
        # Only submitted fields are validated and changed.
        data = _input(request)
        errors = _validate(data, {key: RULES[key] for key in data})
        if errors:
            return self.json({"errors": errors}, 422)
        item.fill(_clean(data))
        item.save()
        return self.json({"data": {{resource_class}}(item).to_array()})

    def destroy(self, request: Request, id: str) -> JsonResponse:
        _find(id).delete()
        return self.json(None, 204)
"""
)

_ROUTES_BLOCK = """\
    # {{class}} API resource — every route requires an authenticated user. Add
    # your own authorization (ownership, roles) in the controller before going live.
    {{mw_import}}
    from app.controllers.{{snake}}_controller import {{class}}Controller
    _c = {{class}}Controller()
    with router.group(middleware=[{{mw_class}}]):
        router.get("/api/{{prefix}}", _c.index).named("api.{{prefix}}.index")
        router.post("/api/{{prefix}}", _c.store).named("api.{{prefix}}.store")
        router.get("/api/{{prefix}}/{id}", _c.show).named("api.{{prefix}}.show")
        router.put("/api/{{prefix}}/{id}", _c.update).named("api.{{prefix}}.update")
        router.delete("/api/{{prefix}}/{id}", _c.destroy).named("api.{{prefix}}.destroy")\
"""

_ROUTES_BLOCK_PUBLIC = """\
    # {{class}} API resource — PUBLIC: generated with --public, so anyone can
    # create, change and delete records. Protect the write routes before going live.
    from app.controllers.{{snake}}_controller import {{class}}Controller
    _c = {{class}}Controller()
    router.get("/api/{{prefix}}", _c.index).named("api.{{prefix}}.index")
    router.post("/api/{{prefix}}", _c.store).named("api.{{prefix}}.store")
    router.get("/api/{{prefix}}/{id}", _c.show).named("api.{{prefix}}.show")
    router.put("/api/{{prefix}}/{id}", _c.update).named("api.{{prefix}}.update")
    router.delete("/api/{{prefix}}/{id}", _c.destroy).named("api.{{prefix}}.destroy")\
"""
