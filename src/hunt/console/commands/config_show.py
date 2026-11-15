from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import click

_SENSITIVE_RE = re.compile(
    r"(secret|key|password|passwd|token|auth|private|credential|api_key|access)",
    re.IGNORECASE,
)
_REDACTED = "*** redacted ***"

# Keys that conventionally hold connection strings (database.url, sentry.dsn, redis.uri, ...).
_URL_KEY_RE = re.compile(r"(?:^|[._-])(url|dsn|uri)(?:$|[._-])", re.IGNORECASE)
# scheme://user:password@host — the password part of any URL-like value.
_URL_PASSWORD_RE = re.compile(r"(?P<head>\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@?#]*):[^\s/@?#]*@")
# scheme://userinfo@host — any userinfo at all (a DSN keeps its secret in the user part).
_URL_USERINFO_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/@?#]+@")


def _mask_url_credentials(value: str) -> str:
    """Mask the password in ``scheme://user:password@host`` wherever it appears in *value*."""
    return _URL_PASSWORD_RE.sub(lambda m: f"{m.group('head')}:***@", value)


def _redact(value: Any, key: str) -> Any:
    """Replace sensitive string values with a redaction marker."""
    if not isinstance(value, str) or not value:
        return value
    if _SENSITIVE_RE.search(key):
        return _REDACTED
    if _URL_KEY_RE.search(key) and (_URL_USERINFO_RE.search(value) or "?" in value):
        # Connection strings carry credentials in the userinfo or the query string.
        return _REDACTED
    return _mask_url_credentials(value)


def _redact_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return _redact_dict(value, key)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, key) for item in value]
    return _redact(value, key)


def _redact_dict(data: dict, parent_key: str = "") -> dict:
    out: dict = {}
    for k, v in data.items():
        full_key = f"{parent_key}.{k}" if parent_key else k
        out[k] = _redact_value(v, full_key)
    return out


def _load_config() -> dict:
    from dotenv import load_dotenv

    from hunt.console.commands.config_cache import _load_config_dir

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    config_dir = Path.cwd() / "config"
    if not config_dir.exists():
        return {}
    return _load_config_dir(config_dir)


def _get_nested(data: dict, key: str) -> Any:
    """Navigate 'app.name' → data['app']['name']."""
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


@click.command("config:show")
@click.argument("key", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--no-redact", is_flag=True, help="Show sensitive values (use with caution).")
def config_show_command(key: str | None, as_json: bool, no_redact: bool) -> None:
    """Display resolved configuration values (sensitive keys are redacted).

    Pass an optional KEY (e.g. 'app' or 'app.name') to narrow the output.
    """
    try:
        config = _load_config()
    except Exception as e:
        click.echo(f"  Could not load config: {e}", err=True)
        raise SystemExit(1) from e

    if not config:
        click.echo("  No configuration found (config/ directory missing or empty).")
        return

    if key:
        value = _get_nested(config, key)
        if value is None:
            click.echo(f"  Key '{key}' not found in configuration.", err=True)
            raise SystemExit(1)
        # Redact against the full dotted key so `config:show database.password` is still masked.
        if not no_redact:
            value = _redact_value(value, key)
        data = {key: value} if not isinstance(value, dict) else value
    else:
        data = config if no_redact else _redact_dict(config)

    if as_json:
        click.echo(json.dumps(data, default=str, indent=2))
        return

    _print_table(data)


def _print_table(data: dict, prefix: str = "") -> None:
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            _print_table(v, full_key)
        else:
            val_str = str(v) if v is not None else "null"
            click.echo(f"  {full_key:<45} {val_str}")
