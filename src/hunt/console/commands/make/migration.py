from __future__ import annotations

import time
from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:migration")
@click.argument("name")
@click.option("--create", default=None, metavar="TABLE", help="Table to create")
@click.option("--table", default=None, metavar="TABLE", help="Table to modify")
def make_migration_command(name: str, create: str | None, table: str | None) -> None:
    """Create a new migration file."""
    _create_migration(name, create, table)


def _create_migration(name: str, create: str | None = None, table: str | None = None) -> None:
    timestamp = time.strftime("%Y_%m_%d_%H%M%S")
    filename = f"{timestamp}_{Str.snake(name)}"
    class_name = Str.pascal(name)

    if create:
        stub = _CREATE_STUB.replace("{{table}}", create)
    elif table:
        stub = _UPDATE_STUB.replace("{{table}}", table)
    else:
        # Infer from name
        if name.startswith("create_") and name.endswith("_table"):
            inferred = name[7:-6]
            stub = _CREATE_STUB.replace("{{table}}", inferred)
        else:
            stub = _BLANK_STUB

    content = stub.replace("{{class}}", class_name)

    out = Path.cwd() / "database" / "migrations" / f"{filename}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    click.echo(f"  Created Migration: {out.relative_to(Path.cwd())}")


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
