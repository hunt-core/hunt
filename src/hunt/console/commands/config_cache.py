from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import click


def _load_config_dir(config_dir: Path) -> dict:
    """Import each config/*.py and collect its top-level dicts."""
    merged: dict = {}
    sys.path.insert(0, str(config_dir.parent))
    for cfg_file in sorted(config_dir.glob("*.py")):
        if cfg_file.name.startswith("_"):
            continue
        key = cfg_file.stem
        spec = importlib.util.spec_from_file_location(f"_config_{key}", cfg_file)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            continue

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


@click.command("config:cache")
def config_cache_command() -> None:
    """Cache all configuration files into a single serialised file."""
    config_dir = Path.cwd() / "config"
    if not config_dir.exists():
        click.echo("  config/ directory not found.", err=True)
        return

    merged = _load_config_dir(config_dir)
    out = Path.cwd() / "storage" / "framework" / "config.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, default=str, indent=2), encoding="utf-8")
    click.echo(f"  Configuration cached successfully [{out.relative_to(Path.cwd())}].")


@click.command("config:clear")
def config_clear_command() -> None:
    """Remove the cached configuration file."""
    out = Path.cwd() / "storage" / "framework" / "config.json"
    if out.exists():
        out.unlink()
        click.echo("  Configuration cache cleared.")
    else:
        click.echo("  Configuration cache is already empty.")
