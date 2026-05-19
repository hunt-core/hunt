from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import click

_DOTTED_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$")


def _coerce(value: str) -> object:
    """Parse a --data value: try JSON first, fall back to raw string."""
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value


def _parse_data(data: tuple[str, ...]) -> dict:
    kwargs: dict = {}
    for pair in data:
        if "=" not in pair:
            raise click.BadParameter(f"Expected key=value, got {pair!r}", param_hint="--data")
        key, _, raw_val = pair.partition("=")
        kwargs[key.strip()] = _coerce(raw_val)
    return kwargs


def _resolve_by_name(name: str, jobs_dir: Path) -> type | None:
    """Find a Job class whose .name attribute or class name matches `name`."""
    from hunt.console.commands.job_list import _discover_jobs

    for j in _discover_jobs(jobs_dir):
        if j["name"] == name or j["class_name"] == name:
            return j["cls"]
    return None


def _resolve_by_dotted_path(dotted: str) -> type:
    from hunt.queue.job import Job

    if not _DOTTED_PATH_RE.match(dotted):
        raise click.BadParameter(f"Malformed class path: {dotted!r}")
    module_path, class_name = dotted.rsplit(".", 1)
    try:
        mod = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        raise click.ClickException(f"Could not import module '{module_path}': {exc}") from exc
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise click.ClickException(f"Class '{class_name}' not found in '{module_path}'.")
    if not (isinstance(cls, type) and issubclass(cls, Job)):
        raise click.ClickException(f"'{dotted}' is not a Job subclass.")
    return cls


@click.command("job:run")
@click.argument("name")
@click.option(
    "--data",
    multiple=True,
    metavar="KEY=VALUE",
    help="Constructor argument as key=value. Repeat for multiple. Values are JSON-decoded (1→int, true→bool, etc.).",
)
@click.option("--queue", default=None, help="Override the queue name stored in the jobs table (no effect for sync run).")
def job_run_command(name: str, data: tuple[str, ...], queue: str | None) -> None:
    """Run a job synchronously.

    NAME can be a short name (matches the job's `name` attribute or class name),
    or a full dotted class path like app.jobs.my_job.MyJob.

    \b
    Examples:
      hunt job:run send_welcome_email
      hunt job:run SendWelcomeEmail --data user_id=42
      hunt job:run app.jobs.reports.GenerateReport --data month=2026-05
    """
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    sys.path.insert(0, str(Path.cwd()))

    kwargs = _parse_data(data)

    cls: type | None = None

    # Dotted path takes priority when it contains at least one dot
    if "." in name:
        cls = _resolve_by_dotted_path(name)
    else:
        jobs_dir = Path.cwd() / "app" / "jobs"
        if jobs_dir.is_dir():
            cls = _resolve_by_name(name, jobs_dir)
        # Fall back to treating it as a bare module.ClassName if it has a dot
        # (already handled above). If still not found, give a clear error.

    if cls is None:
        raise click.ClickException(
            f"Job {name!r} not found. "
            "Run `hunt job:list` to see available jobs, "
            "or pass the full dotted class path."
        )

    try:
        instance = cls(**kwargs)
    except TypeError as exc:
        raise click.ClickException(f"Could not instantiate {cls.__name__}: {exc}") from exc

    click.echo(f"  Running {cls.__name__}...")
    try:
        instance.handle()
        click.echo(f"  Done.")
    except Exception as exc:
        raise click.ClickException(f"{cls.__name__} raised an exception: {exc}") from exc
