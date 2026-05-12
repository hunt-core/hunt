from __future__ import annotations

import os
from pathlib import Path

import click


@click.command("serve")
@click.option("--host", default="127.0.0.1", help="Host to bind")
@click.option("--port", default=8000, type=int, help="Port to bind")
@click.option("--reload/--no-reload", default=True, help="Auto-reload on file changes")
def serve_command(host: str, port: int, reload: bool) -> None:
    """Start the development server."""
    import uvicorn

    app_dir = str(Path.cwd())
    click.echo(f"  hunt  dev server running at http://{host}:{port}")
    click.echo("  Press Ctrl+C to stop.\n")

    uvicorn.run(
        "bootstrap.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        app_dir=app_dir,
    )
