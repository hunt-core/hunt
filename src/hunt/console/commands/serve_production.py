from __future__ import annotations

import os
from pathlib import Path

import click

from hunt.application import app_env


def _default_workers() -> int:
    try:
        from multiprocessing import cpu_count

        return cpu_count() * 2 + 1
    except NotImplementedError:
        return 3


def _debug_enabled() -> bool:
    return os.environ.get("APP_DEBUG", "false").strip().lower() in ("true", "1", "yes", "on")


def _warn_if_misconfigured() -> None:
    issues = []
    if app_env() != "production":
        issues.append("APP_ENV is not set to 'production'")
    if _debug_enabled():
        issues.append("APP_DEBUG=true — set it to false in production")
    if not os.environ.get("APP_KEY"):
        issues.append("APP_KEY is not set — run: hunt key:generate")
    if not any(os.environ.get(key) for key in ("DATABASE_URL", "DB_CONNECTION", "DB_HOST")):
        issues.append("No database configured — set DB_CONNECTION (or DATABASE_URL) in .env")

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
@click.option(
    "--proxy-headers/--no-proxy-headers",
    "proxy_headers",
    default=None,
    help=(
        "Trust X-Forwarded-For / X-Forwarded-Proto from the addresses in --forwarded-allow-ips. "
        "Off by default; turned on when --forwarded-allow-ips is given. "
        "The application-level equivalent is the TrustProxies middleware."
    ),
)
@click.option(
    "--forwarded-allow-ips",
    default=None,
    help=(
        "Comma-separated proxy addresses (or CIDR ranges) whose forwarded headers are trusted, "
        "for example the load balancer address. Avoid '*' unless the port is unreachable except through the proxy."
    ),
)
@click.option(
    "--allow-debug",
    is_flag=True,
    default=False,
    help="Start even though APP_ENV=production and APP_DEBUG is true (leaks stack traces).",
)
def serve_production_command(
    host: str,
    port: int,
    workers: int | None,
    proxy_headers: bool | None,
    forwarded_allow_ips: str | None,
    allow_debug: bool,
) -> None:
    """Start a production-grade server (multiple workers, no reload).

    Behind a reverse proxy, pass --forwarded-allow-ips with the proxy address (and
    register TrustProxies in the application) so client addresses, and with them
    rate limits, are per client rather than per proxy.
    """
    import sys

    from hunt.console.commands.env_check import check_required

    missing = check_required()
    if missing:
        click.echo("  Error: required environment variables are not set:", err=True)
        for var in missing:
            click.echo(f"    ✗ {var}", err=True)
        click.echo("  Run `hunt env:check` for details.", err=True)
        sys.exit(1)

    if app_env() == "production" and _debug_enabled() and not allow_debug:
        click.echo(
            "  Error: APP_DEBUG is true while APP_ENV=production. Debug mode exposes stack traces and "
            "configuration to visitors.",
            err=True,
        )
        click.echo("  Set APP_DEBUG=false, or pass --allow-debug to start anyway.", err=True)
        sys.exit(1)

    if proxy_headers is None:
        proxy_headers = forwarded_allow_ips is not None
    if proxy_headers and forwarded_allow_ips is None:
        # Only a proxy on the same host is trusted unless told otherwise.
        forwarded_allow_ips = "127.0.0.1"

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
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips if proxy_headers else None,
        app_dir=str(cwd),
    )
