from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:admin-resource")
@click.argument("name")
@click.option(
    "--model",
    default=None,
    help="Model class name (derived from name by stripping 'Resource' if omitted).",
)
def make_admin_resource_command(name: str, model: str | None) -> None:
    """Generate a stub AdminResource class in app/admin/."""

    # Normalise the resource class name to PascalCase
    class_name = Str.pascal(name) if not name[0].isupper() else name

    # Derive model name by stripping trailing "Resource" if the option is omitted
    if model is None:
        raw_model = class_name[: -len("Resource")] if class_name.endswith("Resource") else class_name
    else:
        raw_model = model

    model_class = Str.pascal(raw_model) if not raw_model[0].isupper() else raw_model
    model_lower = Str.snake(model_class)
    snake_name = Str.snake(class_name)

    target_dir = Path.cwd() / "app" / "admin"
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"{snake_name}.py"
    if file_path.exists():
        click.echo(f"  File already exists: {file_path}", err=True)
        raise SystemExit(1)

    content = (
        "from hunt.admin import AdminResource\n"
        "from hunt.admin.fields import Text, Number, DateTime\n"
        f"from app.models.{model_lower} import {model_class}\n"
        "\n\n"
        f"class {class_name}(AdminResource):\n"
        f"    model = {model_class}\n"
        f'    label = "{model_class}"\n'
        '    search_columns = ["id"]\n'
        '    default_order = ("id", "desc")\n'
        "    per_page = 15\n"
        "\n"
        "    def fields(self):\n"
        "        return [\n"
        '            Text("Id", attribute="id").sortable().readonly(),\n'
        "            # TODO: add your fields here\n"
        '            DateTime("Created At", attribute="created_at").sortable(),\n'
        '            DateTime("Updated At", attribute="updated_at").sortable(),\n'
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

    file_path.write_text(content)
    click.echo(f"  AdminResource created: {file_path}")
