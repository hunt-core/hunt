from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:model")
@click.argument("name")
@click.option("-m", "--migration", "with_migration", is_flag=True, help="Create a migration for the model")
@click.option("-c", "--controller", "with_controller", is_flag=True, help="Create a controller for the model")
@click.option("-f", "--factory", "with_factory", is_flag=True, help="Create a factory for the model")
@click.option("-s", "--seeder", "with_seeder", is_flag=True, help="Create a seeder for the model")
@click.option("-p", "--policy", "with_policy", is_flag=True, help="Create a policy for the model")
@click.option(
    "--all",
    "all_files",
    is_flag=True,
    help="Shorthand for -m -c -f -s -p",
)
@click.option(
    "--fields",
    default="",
    metavar="FIELDS",
    help='Column definitions, e.g. "title:string body:text published:boolean"',
)
@click.option("--dry-run", is_flag=True, help="Preview files without writing them")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_model_command(
    name: str,
    with_migration: bool,
    with_controller: bool,
    with_factory: bool,
    with_seeder: bool,
    with_policy: bool,
    all_files: bool,
    fields: str,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Create a new model class."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)

    if all_files:
        with_migration = with_controller = with_factory = with_seeder = with_policy = True

    _create_model(name, fields=fields)

    if with_migration:
        from hunt.console.commands.make.migration import _create_migration

        table = Str.plural(Str.snake(name))
        _create_migration(f"create_{table}_table", table, fields=fields)

    if with_controller:
        from hunt.console.commands.make.controller import _create_controller

        _create_controller(f"{Str.pascal(name)}Controller", resource=False)

    if with_factory:
        from hunt.console.commands.make.factory import _create_factory

        _create_factory(name)

    if with_seeder:
        from hunt.console.commands.make.seeder import _create_seeder

        _create_seeder(name)

    if with_policy:
        from hunt.console.commands.make.policy import _create_policy

        _create_policy(f"{Str.pascal(name)}Policy", model=name)

    output.finish()


def _create_model(name: str, fields: str = "") -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._output import output
    from hunt.console.commands.make.field_types import fillable_list, parse_fields

    class_name = Str.pascal(name)
    table = Str.plural(Str.snake(name))
    parsed = parse_fields(fields)
    fillable = fillable_list(parsed) if parsed else "[]"

    stub = load_stub("model", _MODEL_STUB)
    content = stub.replace("{{class}}", class_name).replace("{{table}}", table).replace("{{fillable}}", fillable)

    out = Path.cwd() / "app" / "models" / f"{Str.snake(name)}.py"
    output.write(out, content, label="Created Model     ")


_MODEL_STUB = """\
from hunt.database.model import Model


class {{class}}(Model):
    table = "{{table}}"
    fillable: list[str] = {{fillable}}
    hidden: list[str] = []
"""
