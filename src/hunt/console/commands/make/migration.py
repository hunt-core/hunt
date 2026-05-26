from __future__ import annotations

import time
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


def _create_migration(
    name: str, create: str | None = None, table: str | None = None, fields: str = ""
) -> None:
    from hunt.console.commands.make._output import output

    timestamp = time.strftime("%Y_%m_%d_%H%M%S")
    filename = f"{timestamp}_{Str.snake(name)}"
    class_name = Str.pascal(name)

    if create:
        stub = _create_stub_for(create, fields)
    elif table:
        stub = _UPDATE_STUB.replace("{{table}}", table)
    else:
        if name.startswith("create_") and name.endswith("_table"):
            inferred = name[7:-6]
            stub = _create_stub_for(inferred, fields)
        else:
            stub = _BLANK_STUB

    content = stub.replace("{{class}}", class_name)
    out = Path.cwd() / "database" / "migrations" / f"{filename}.py"
    output.write(out, content, label="Created Migration ")


def _create_stub_for(table: str, fields: str = "") -> str:
    if not fields:
        return _CREATE_STUB.replace("{{table}}", table)
    from hunt.console.commands.make.field_types import migration_columns, parse_fields

    col_lines = migration_columns(parse_fields(fields))
    body = f"\n{col_lines}\n" if col_lines else ""
    return _CREATE_STUB_FIELDS.replace("{{table}}", table).replace("{{columns}}", body)


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
