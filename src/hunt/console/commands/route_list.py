from __future__ import annotations

import json
import os
import sys

import click


@click.command("route:list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON array.")
def route_list_command(as_json: bool) -> None:
    """List all registered routes."""
    sys.path.insert(0, os.getcwd())
    try:
        from bootstrap.app import application

        router = application.make("router")
    except Exception as e:
        click.echo(f"Could not load routes: {e}", err=True)
        return

    routes = router.routes()
    if not routes:
        if as_json:
            click.echo("[]")
        else:
            click.echo("No routes registered.")
        return

    if as_json:
        rows = []
        for route in routes:
            action = route.action
            action_name = getattr(action, "__qualname__", None) or str(action)
            mw = [m.__name__ if isinstance(m, type) else str(m) for m in route._middleware]
            rows.append(
                {
                    "method": "|".join(route.methods),
                    "uri": route.uri,
                    "name": route.name or "",
                    "action": action_name,
                    "middleware": mw,
                }
            )
        click.echo(json.dumps(rows, indent=2))
        return

    click.echo(f"{'Method':<10} {'URI':<40} {'Name':<25} {'Middleware'}")
    click.echo("-" * 95)
    for route in routes:
        methods = click.style("|".join(route.methods), fg="green")
        name = route.name or ""
        mw = ", ".join(m.__name__ if isinstance(m, type) else str(m) for m in route._middleware)
        click.echo(f"{methods:<18} {route.uri:<40} {name:<25} {mw}")
