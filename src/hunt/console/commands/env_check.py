from __future__ import annotations

import os
import sys
from pathlib import Path

import click

_REQUIRED: list[tuple[str, str]] = [
    ("APP_KEY", "Application encryption key — run `hunt key:generate` to create one"),
    ("DATABASE_URL", "Primary database connection string"),
]

_RECOMMENDED: list[tuple[str, str, str]] = [
    ("APP_ENV", "production", "Should be 'production' in deployed environments"),
    ("APP_DEBUG", "false", "Must be 'false' in production — leaks stack traces if true"),
    ("SESSION_SECURE", "true", "Cookies sent over HTTPS only — set true behind TLS"),
    ("LOG_FORMAT", "json", "Structured JSON logs are easier to ship to aggregators"),
]

_OPTIONAL_WITH_SIDE_EFFECTS: list[tuple[str, str]] = [
    ("SENTRY_DSN", "Sentry error tracking — install sentry-sdk and set this to enable"),
    ("MAIL_HOST", "Outbound mail server — required for password reset and email notifications"),
    ("REDIS_URL", "Redis — required for multi-worker rate limiting and Redis-backed sessions"),
    ("HEALTH_CHECK_ENABLED", "Set 'false' to disable the built-in GET /health endpoint"),
]


@click.command("env:check")
def env_check_command() -> None:
    """Validate environment configuration for production deployment."""
    _load_dotenv()

    passed = 0
    failed = 0
    warnings = 0

    click.echo()
    click.echo("  Environment check")
    click.echo("  " + "─" * 50)

    # Required vars
    click.echo()
    click.echo("  Required")
    for key, description in _REQUIRED:
        val = os.environ.get(key, "")
        if val:
            click.echo(f"    {click.style('✓', fg='green')} {key}")
            passed += 1
        else:
            click.echo(f"    {click.style('✗', fg='red')} {key}  —  {description}")
            failed += 1

    # Recommended settings
    click.echo()
    click.echo("  Recommended for production")
    for key, expected, description in _RECOMMENDED:
        val = os.environ.get(key, "")
        if val.lower() == expected.lower():
            click.echo(f"    {click.style('✓', fg='green')} {key}={val}")
            passed += 1
        else:
            display = f"{key}={val!r}" if val else key
            click.echo(f"    {click.style('!', fg='yellow')} {display}  —  {description}")
            warnings += 1

    # Optional / informational
    click.echo()
    click.echo("  Optional")
    for key, description in _OPTIONAL_WITH_SIDE_EFFECTS:
        val = os.environ.get(key, "")
        if val:
            click.echo(f"    {click.style('✓', fg='green')} {key}={val}")
        else:
            click.echo(f"    {click.style('-', fg='bright_black')} {key}  —  {description}")

    # Summary
    click.echo()
    click.echo("  " + "─" * 50)
    status_parts = [f"{passed} passed"]
    if warnings:
        status_parts.append(click.style(f"{warnings} warnings", fg="yellow"))
    if failed:
        status_parts.append(click.style(f"{failed} failed", fg="red"))
    click.echo("  " + ", ".join(status_parts))
    click.echo()

    if failed:
        sys.exit(1)


def _load_dotenv() -> None:
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except ImportError:
            pass
