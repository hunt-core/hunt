from __future__ import annotations

import sys
import os
import click


@click.command("route:list")
def route_list_command() -> None:
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
        click.echo("No routes registered.")
        return

    click.echo(f"{'Method':<10} {'URI':<40} {'Name':<25} {'Middleware'}")
    click.echo("-" * 95)
    for route in routes:
        methods = click.style("|".join(route.methods), fg="green")
        name = route.name or ""
        mw = ", ".join(
            m.__name__ if isinstance(m, type) else str(m)
            for m in route.middleware
        )
        click.echo(f"{methods:<18} {route.uri:<40} {name:<25} {mw}")
