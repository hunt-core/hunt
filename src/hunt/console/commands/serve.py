from __future__ import annotations

import os
import threading
import time
import webbrowser
from pathlib import Path

import click

_WATCH_DIRS = ("app", "config", "resources", "routes")


@click.command("serve")
@click.option("--host", default="127.0.0.1", help="Host to bind")
@click.option("--port", default=8000, type=int, help="Port to bind")
@click.option("--reload/--no-reload", default=True, help="Auto-reload on file changes")
@click.option("--open", "open_browser", is_flag=True, help="Open the app in the default browser after starting")
def serve_command(host: str, port: int, reload: bool, open_browser: bool) -> None:
    """Start the development server."""
    import uvicorn

    cwd = Path.cwd()
    app_dir = str(cwd)
    url = f"http://{host}:{port}"

    click.echo(f"  hunt  dev server running at {url}")

    reload_dirs: list[str] | None = None
    if reload:
        reload_dirs = [str(cwd / d) for d in _WATCH_DIRS if (cwd / d).is_dir()]
        debug = os.environ.get("APP_DEBUG", "false").lower() == "true"
        if debug and reload_dirs:
            watching = ", ".join(f"{d}/" for d in _WATCH_DIRS if (cwd / d).is_dir())
            click.echo(f"  Watching for changes in: {watching}")

    click.echo("  Press Ctrl+C to stop.\n")

    if open_browser:
        _open_after(url, delay=1.5)

    uvicorn.run(
        "bootstrap.app:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=reload_dirs,
        log_level="info",
        app_dir=app_dir,
    )


def _open_after(url: str, delay: float) -> None:
    def _open() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    t = threading.Thread(target=_open, daemon=True)
    t.start()
