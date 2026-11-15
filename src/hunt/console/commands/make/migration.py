from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:migration")
@click.argument("name")
@click.option("--create", default=None, metavar="TABLE", help="Table to create")
@click.option("--table", default=None, metavar="TABLE", help="Table to modify")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_migration_command(name: str, create: str | None, table: str | None, dry_run: bool, as_json: bool) -> None:
    """Create a new migration file."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)
    _create_migration(name, create, table)
    output.finish()


def _create_migration(name: str, create: str | None = None, table: str | None = None, fields: str = "") -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._naming import migration_timestamp, validate_identifier
    from hunt.console.commands.make._output import output

    validate_identifier(name, "migration name")
    if create:
        validate_identifier(create, "table name")
    if table:
        validate_identifier(table, "table name")

    class_name = Str.pascal(name)

    if create:
        stub = _create_stub_for(create, fields)
    elif table:
        stub = load_stub("migration.update", _UPDATE_STUB).replace("{{table}}", table)
    else:
        if name.startswith("create_") and name.endswith("_table") and len(name) > len("create__table"):
            inferred = name[7:-6]
            stub = _create_stub_for(inferred, fields)
        else:
            stub = load_stub("migration.blank", _BLANK_STUB)

    content = stub.replace("{{class}}", class_name)
    filename = f"{migration_timestamp()}_{Str.snake(name)}"
    out = Path.cwd() / "database" / "migrations" / f"{filename}.py"
    output.write(out, content, label="Created Migration ")


def _create_stub_for(table: str, fields: str = "") -> str:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._naming import validate_identifier
    from hunt.console.commands.make.field_types import migration_columns, parse_fields

    validate_identifier(table, "table name")
    col_lines = migration_columns(parse_fields(fields)) if fields else ""
    body = f"\n{col_lines}\n" if col_lines else ""
    # An application can override the stub with stubs/migration.stub ({{class}}, {{table}}, {{columns}}).
    stub = load_stub("migration", _CREATE_STUB_FIELDS)
    return stub.replace("{{table}}", table).replace("{{columns}}", body)


_CREATE_STUB_FIELDS = """\
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

_CREATE_STUB = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.blueprint import Blueprint
from hunt.database.schema.migration import Migration


class {{class}}(Migration):
    def up(self) -> None:
        Schema.create("{{table}}", lambda bp: [
            bp.id(),
            bp.timestamps(),
        ])

    def down(self) -> None:
        Schema.drop_if_exists("{{table}}")
"""

_UPDATE_STUB = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.blueprint import Blueprint
from hunt.database.schema.migration import Migration


class {{class}}(Migration):
    def up(self) -> None:
        Schema.table("{{table}}", lambda bp: [
            # bp.string("new_column"),
        ])

    def down(self) -> None:
        Schema.table("{{table}}", lambda bp: [
            # bp.drop_column("new_column"),
        ])
"""

_BLANK_STUB = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.blueprint import Blueprint
from hunt.database.schema.migration import Migration


class {{class}}(Migration):
    def up(self) -> None:
        pass

    def down(self) -> None:
        pass
"""
