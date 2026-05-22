from __future__ import annotations

import os
from pathlib import Path

import click


def _default_workers() -> int:
    try:
        from multiprocessing import cpu_count
        return cpu_count() * 2 + 1
    except NotImplementedError:
        return 3


def _warn_if_misconfigured() -> None:
    issues = []
    if os.environ.get("APP_ENV", "local") != "production":
        issues.append("APP_ENV is not set to 'production'")
    if os.environ.get("APP_DEBUG", "false").lower() == "true":
        issues.append("APP_DEBUG=true — set it to false in production")
    if not os.environ.get("APP_KEY"):
        issues.append("APP_KEY is not set — run: hunt key:generate")
    if not os.environ.get("DATABASE_URL") and not os.environ.get("DB_HOST"):
        issues.append("No database configured — set DATABASE_URL or DB_HOST in .env")

    if issues:
        click.echo("  Configuration warnings:", err=True)
        for issue in issues:
            click.echo(f"    [!] {issue}", err=True)
        click.echo("", err=True)


@click.command("serve:production")
@click.option("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
@click.option("--port", default=8000, type=int, help="Port to bind")
@click.option(
    "--workers",
    default=None,
    type=int,
    help="Worker processes (default: 2 x CPU + 1)",
)
def serve_production_command(host: str, port: int, workers: int | None) -> None:
    """Start a production-grade server (multiple workers, no reload)."""
    import uvicorn

    n_workers = workers if workers is not None else _default_workers()
    cwd = Path.cwd()
    url = f"http://{host}:{port}"

    _warn_if_misconfigured()

    click.echo(f"  hunt  production server running at {url}")
    click.echo(f"  Workers: {n_workers}  (override with --workers N)")
    click.echo("  Press Ctrl+C to stop.\n")

    uvicorn.run(
        "bootstrap.app:app",
        host=host,
        port=port,
        workers=n_workers,
        reload=False,
        access_log=True,
        app_dir=str(cwd),
    )
