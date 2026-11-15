from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import click

from hunt.config.loader import config_cache_path, load_config_files, write_config_cache


def _load_config_dir(config_dir: Path) -> dict:
    """Import each config/*.py and collect its top-level dicts.

    A config file that fails to import aborts the command with the failing
    file named, instead of being silently left out of the result.
    """
    merged: dict = {}
    parent = str(config_dir.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    for cfg_file in sorted(config_dir.glob("*.py")):
        if cfg_file.name.startswith("_"):
            continue
        key = cfg_file.stem
        spec = importlib.util.spec_from_file_location(f"_config_{key}", cfg_file)
        if spec is None or spec.loader is None:
            raise click.ClickException(f"Could not load config file config/{cfg_file.name}.")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            raise click.ClickException(f"Failed to load config/{cfg_file.name}: {type(exc).__name__}: {exc}") from exc

        # Collect the first top-level non-private dict, or all of them
        data: dict = {}
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            val = getattr(mod, attr)
            if isinstance(val, dict):
                data[attr] = val
        if data:
            merged[key] = data if len(data) > 1 else next(iter(data.values()))
    return merged


def _unserialisable(value: object, path: str) -> list[str]:
    """Return the dotted paths of every value that JSON cannot represent exactly."""
    if value is None or isinstance(value, (str, bool, int)):
        return []
    if isinstance(value, float):
        return [] if value == value and value not in (float("inf"), float("-inf")) else [f"{path} (non-finite float)"]
    if isinstance(value, list):
        found: list[str] = []
        for index, item in enumerate(value):
            found.extend(_unserialisable(item, f"{path}[{index}]"))
        return found
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if not isinstance(key, str):
                found.append(f"{child} (key is {type(key).__name__}, not str)")
                continue
            found.extend(_unserialisable(item, child))
        return found
    return [f"{path} ({type(value).__name__})"]


@click.command("config:cache")
def config_cache_command() -> None:
    """Cache all configuration files into a single file that the application loads at boot.

    The cache contains every resolved value, including secrets read from the
    environment, so it is written with mode 0600 and must never be committed
    or copied into an image layer that is shared. While it exists, changes to
    config/*.py and to environment variables are ignored: re-run this command
    (or `hunt config:clear`) after changing either.
    """
    root = Path.cwd()
    config_dir = root / "config"
    if not config_dir.exists():
        click.echo("  config/ directory not found.", err=True)
        return

    env_file = root / ".env"
    if env_file.exists():
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)

    parent = str(root)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    try:
        # Same loader (and therefore the same shape) the application uses at boot.
        merged = load_config_files(config_dir)
    except Exception as exc:
        click.echo(f"  Failed to load configuration: {type(exc).__name__}: {exc}", err=True)
        click.echo("  Nothing was written.", err=True)
        raise SystemExit(1) from exc

    problems = _unserialisable(merged, "")
    if problems:
        click.echo("  Configuration was not cached: these values cannot be stored as JSON without loss:", err=True)
        for problem in problems[:20]:
            click.echo(f"    - {problem}", err=True)
        if len(problems) > 20:
            click.echo(f"    ... and {len(problems) - 20} more", err=True)
        click.echo(
            "  Expose each config file's settings as a top-level `config = {...}` dict of plain\n"
            "  str/int/float/bool/None/list/dict values (convert Path objects with str()).",
            err=True,
        )
        raise SystemExit(1)

    out = config_cache_path(config_dir)
    try:
        write_config_cache(out, merged)
    except (TypeError, ValueError) as exc:
        click.echo(f"  Configuration was not cached: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"  Configuration cached successfully [{out.relative_to(root)}].")
    click.echo("  The file contains secrets (mode 0600). Run `hunt config:clear` after changing config or .env.")


@click.command("config:clear")
def config_clear_command() -> None:
    """Remove the cached configuration file."""
    out = config_cache_path(Path.cwd() / "config")
    if out.is_symlink() or out.exists():
        out.unlink()
        click.echo("  Configuration cache cleared.")
    else:
        click.echo("  Configuration cache is already empty.")
