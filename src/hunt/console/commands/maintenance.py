from __future__ import annotations

import json
import os
import re
from pathlib import Path

import click

_SENTINEL = ".maintenance"


def _sentinel_path() -> Path:
    """Return the maintenance sentinel the MaintenanceMode middleware reads.

    The path is owned by the middleware module so the CLI and the running
    application can never disagree about where the file lives.
    """
    try:
        from hunt.http.middleware.maintenance import maintenance_file
    except ImportError:
        # Older middleware without the shared helper: it looks in the working directory.
        return Path.cwd() / _SENTINEL
    return Path(maintenance_file())


def _middleware_registered(root: Path) -> bool:
    """Best-effort check that bootstrap/app.py references the MaintenanceMode middleware."""
    bootstrap = root / "bootstrap" / "app.py"
    try:
        source = bootstrap.read_text(encoding="utf-8")
    except OSError:
        return False
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    return re.search(r"\bMaintenanceMode\b", code) is not None


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


@click.command("down")
@click.option("--message", default="We'll be back shortly. Please try again later.", help="Message shown to visitors.")
@click.option(
    "--retry", default=60, type=click.IntRange(min=0), help="Retry-After seconds sent in the response header."
)
def down_command(message: str, retry: int) -> None:
    """Put the application into maintenance mode (returns 503 to all visitors)."""
    sentinel = _sentinel_path()
    payload = json.dumps({"message": message, "retry_after": retry}, indent=2)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    tmp = sentinel.with_name(sentinel.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, sentinel)

    click.echo("  Application is now in maintenance mode.")
    click.echo(f"  Message : {message}")
    click.echo(f"  Retry-After : {retry}s")
    click.echo(f"  Sentinel : {_display(sentinel)}")
    if not _middleware_registered(Path.cwd()):
        click.echo(
            "  Warning: MaintenanceMode does not appear to be registered in bootstrap/app.py,\n"
            "  so the application will keep serving requests. Add it first in global_middleware:\n"
            "      from hunt.http.middleware.maintenance import MaintenanceMode\n"
            "      global_middleware = [MaintenanceMode, ...]",
            err=True,
        )
    click.echo("  Run `hunt up` to bring it back online.")


@click.command("up")
def up_command() -> None:
    """Take the application out of maintenance mode."""
    sentinel = _sentinel_path()
    if sentinel.is_symlink() or sentinel.exists():
        sentinel.unlink()
        click.echo("  Application is now live.")
    else:
        click.echo("  Application is already live (no .maintenance file found).")
