from __future__ import annotations

import base64
import os
import re
import sys
from pathlib import Path

import click

_APP_KEY_LINE = re.compile(r"(?m)^APP_KEY=(.*)$")

_ROTATION_WARNING = (
    "  An application key is already set. Replacing it invalidates everything derived from it:\n"
    "    - signed queue payloads and signed URLs already issued\n"
    "    - encrypted values (for example stored two-factor secrets)\n"
    "    - existing sessions and remember-me cookies"
)


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _write_atomic(path: Path, content: str) -> None:
    """Replace *path* with *content* through a 0600 temp file in the same directory."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@click.command("key:generate")
@click.option("--show", is_flag=True, default=False, help="Print key without writing to .env")
@click.option("--force", is_flag=True, default=False, help="Replace an existing key without asking")
def key_generate_command(show: bool, force: bool) -> None:
    """Generate a new application key and write it to .env."""
    key = "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode()

    if show:
        click.echo(key)
        return

    env_file = Path.cwd() / ".env"
    if not env_file.exists():
        click.echo("  No .env file found. Run `hunt new` to scaffold a project first.")
        return

    content = env_file.read_text(encoding="utf-8")
    match = _APP_KEY_LINE.search(content)
    existing = match.group(1).strip().strip("\"'") if match else ""

    if existing and not force:
        click.echo(_ROTATION_WARNING, err=True)
        if not _is_interactive():
            click.echo("  Refusing to rotate the key. Re-run with --force to replace it.", err=True)
            raise SystemExit(1)
        if not click.confirm("  Replace the existing application key?", default=False):
            click.echo("  Application key left unchanged.")
            raise SystemExit(1)

    if match:
        # Only the APP_KEY line itself: STRIPE_APP_KEY=, OLD_APP_KEY= and comments are left alone.
        content = _APP_KEY_LINE.sub(lambda _m: f"APP_KEY={key}", content, count=1)
    else:
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"APP_KEY={key}\n"

    _write_atomic(env_file, content)
    click.echo("  Application key set.")
