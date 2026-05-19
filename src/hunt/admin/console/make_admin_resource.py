from __future__ import annotations

import re
from pathlib import Path

import click

from hunt.support.str import Str

_CLASS_RE = re.compile(r"^class ([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE)


@click.command("make:admin-resource")
@click.argument("model")
def make_admin_resource_command(model: str) -> None:
    """Generate a stub AdminResource for MODEL (a filename in app/models/)."""

    model_slug = Str.snake(model.removesuffix(".py")).lower()
    model_file = Path.cwd() / "app" / "models" / f"{model_slug}.py"

    if not model_file.exists():
        raise click.ClickException(
            f"Model file not found: app/models/{model_slug}.py\n"
            f"  Create it first with: hunt make:model {Str.pascal(model_slug)}"
        )

    # Read the class name directly from the file
    model_source = model_file.read_text()
    match = _CLASS_RE.search(model_source)
    if not match:
        raise click.ClickException(f"No class definition found in app/models/{model_slug}.py")
    model_class = match.group(1)

    class_name = f"{model_class}Resource"
    snake_name = Str.snake(class_name)

    # Check not already registered
    routes_file = Path.cwd() / "routes" / "admin.py"
    if routes_file.exists() and f"Admin.resource({class_name})" in routes_file.read_text():
        raise click.ClickException(f"{class_name} is already registered in routes/admin.py")

    target_dir = Path.cwd() / "app" / "admin"
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"{snake_name}.py"
    if file_path.exists():
        raise click.ClickException(f"File already exists: app/admin/{snake_name}.py")

    _DEFAULT_STUB = (
        "from hunt.admin import AdminResource\n"
        "from hunt.admin.fields import Text, Number, DateTime\n"
        "from app.models.{{model_slug}} import {{model_class}}\n"
        "\n\n"
        "class {{class_name}}(AdminResource):\n"
        "    model = {{model_class}}\n"
        '    label = "{{model_class}}"\n'
        '    search_columns = ["id"]\n'
        '    default_order = ("id", "desc")\n'
        "    per_page = 15\n"
        "\n"
        "    def fields(self):\n"
        "        return [\n"
        '            Text("Id", attribute="id").sortable().readonly(),\n'
        "            # TODO: add your fields here\n"
        '            DateTime("Created At", attribute="created_at").sortable().hide_from_forms(),\n'
        '            DateTime("Updated At", attribute="updated_at").sortable().hide_from_forms(),\n'
        "        ]\n"
        "\n"
        "    def filters(self):\n"
        "        return []\n"
        "\n"
        "    def actions(self):\n"
        "        return []\n"
        "\n"
        "    def metrics(self):\n"
        "        return []\n"
    )

    from hunt.console.commands.make import load_stub

    stub = load_stub("admin-resource", _DEFAULT_STUB)
    content = (
        stub.replace("{{class_name}}", class_name)
        .replace("{{model_class}}", model_class)
        .replace("{{model_slug}}", model_slug)
    )

    file_path.write_text(content)
    click.echo(f"  AdminResource created: app/admin/{snake_name}.py")

    _inject_into_routes(model_slug, class_name)


def _inject_into_routes(model_slug: str, class_name: str) -> None:
    routes_file = Path.cwd() / "routes" / "admin.py"
    if not routes_file.exists():
        return

    source = routes_file.read_text()

    import_stmt = f"from app.admin.{Str.snake(class_name)} import {class_name}"
    register_stmt = f"Admin.resource({class_name})"

    if import_stmt in source:
        return

    lines = source.splitlines()

    # Insert import after the last `from app.admin.*` line, falling back to
    # the last import line in the file.
    last_app_admin_import = -1
    last_import = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("from app.admin."):
            last_app_admin_import = i
        if stripped.startswith(("from ", "import ")):
            last_import = i

    import_insert_at = last_app_admin_import if last_app_admin_import >= 0 else last_import
    if import_insert_at >= 0:
        lines.insert(import_insert_at + 1, import_stmt)
    else:
        lines.insert(0, import_stmt)

    # Insert Admin.resource() after the last existing call, or before the
    # first non-import statement (Admin.dashboard, def register, etc.).
    last_resource_call = -1
    first_non_resource_stmt = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("Admin.resource("):
            last_resource_call = i
        elif (
            first_non_resource_stmt < 0
            and stripped
            and not stripped.startswith(("from ", "import ", "#", "Admin.resource("))
        ):
            first_non_resource_stmt = i

    if last_resource_call >= 0:
        lines.insert(last_resource_call + 1, register_stmt)
    elif first_non_resource_stmt >= 0:
        lines.insert(first_non_resource_stmt, register_stmt)
    else:
        lines.append(register_stmt)

    routes_file.write_text("\n".join(lines) + "\n")
    click.echo("  Registered in:        routes/admin.py")
