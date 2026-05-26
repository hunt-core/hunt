from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str


@click.command("make:test")
@click.argument("name")
@click.option("--unit", "test_type", flag_value="unit", default=True, help="Generate a unit test (default)")
@click.option("--feature", "test_type", flag_value="feature", help="Generate a feature (integration) test")
@click.option("--dry-run", is_flag=True, help="Preview without writing")
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def make_test_command(name: str, test_type: str, dry_run: bool, as_json: bool) -> None:
    """Create a new test class."""
    from hunt.console.commands.make._output import output

    output.configure(dry_run=dry_run, as_json=as_json)
    _create_test(name, test_type=test_type)
    output.finish()


def _create_test(name: str, test_type: str = "unit") -> None:
    from hunt.console.commands.make import load_stub
    from hunt.console.commands.make._output import output

    class_name = Str.pascal(name)
    if not class_name.startswith("Test"):
        class_name = f"Test{class_name}"

    if test_type == "feature":
        stub = load_stub("test.feature", _FEATURE_STUB)
        out = Path.cwd() / "tests" / "feature" / f"test_{Str.snake(name)}.py"
    else:
        stub = load_stub("test.unit", _UNIT_STUB)
        out = Path.cwd() / "tests" / "unit" / f"test_{Str.snake(name)}.py"

    content = stub.replace("{{class}}", class_name)
    output.write(out, content, label="Created Test      ")


_UNIT_STUB = """\
from __future__ import annotations

import pytest


class {{class}}:
    def test_example(self) -> None:
        assert True
"""

_FEATURE_STUB = """\
from __future__ import annotations

import pytest
from httpx import AsyncClient


class {{class}}:
    async def test_example(self, client: AsyncClient) -> None:
        response = await client.get("/")
        assert response.status_code == 200
"""
