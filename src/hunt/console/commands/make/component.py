from __future__ import annotations

from pathlib import Path

import click

from hunt.support.str import Str

_STUB = """\
{{--
  Component: {name}
  Props: (list the variables this component expects)
--}}
<div class="{{ class|default('') }}">
    {{- _slot_default|default('')|safe }}
</div>
"""


@click.command("make:component")
@click.argument("name")
def make_component_command(name: str) -> None:
    """Create a new Blade component in resources/views/components/."""
    slug = Str.snake(name).replace("_", "-")
    out = Path.cwd() / "resources" / "views" / "components" / f"{slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        click.echo(f"  Already exists: {out.relative_to(Path.cwd())}")
        return
    out.write_text(_STUB.replace("{name}", slug), encoding="utf-8")
    click.echo(f"  Created Component: {out.relative_to(Path.cwd())}")
    click.echo(f"  Usage: @component('{slug}', {{'prop': value}})")
