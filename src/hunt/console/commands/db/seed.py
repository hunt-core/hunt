from __future__ import annotations

import sys
from pathlib import Path

import click


def _find_seeder(seeder_class: str):
    """Locate a seeder class by searching the database/seeders package and directory."""
    import importlib.util

    seeders_dir = Path.cwd() / "database" / "seeders"

    # 1. Try database.seeders package (__init__.py or module)
    try:
        mod = importlib.import_module("database.seeders")
        cls = getattr(mod, seeder_class, None)
        if cls is not None:
            return cls
    except ImportError:
        pass

    # 2. Try an individual file: database/seeders/{SeederClass}.py
    candidate = seeders_dir / f"{seeder_class}.py"
    if candidate.exists():
        spec = importlib.util.spec_from_file_location(seeder_class, candidate)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, seeder_class, None)

    # 3. Scan all .py files in the directory for the class
    if seeders_dir.is_dir():
        for py_file in sorted(seeders_dir.glob("*.py")):
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:
                continue
            cls = getattr(mod, seeder_class, None)
            if cls is not None:
                return cls

    return None


@click.command("db:seed")
@click.option("--class", "seeder_class", default="DatabaseSeeder", help="Seeder class to run")
def db_seed_command(seeder_class: str) -> None:
    """Run database seeders."""
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    sys.path.insert(0, str(Path.cwd()))

    cls = _find_seeder(seeder_class)
    if cls is None:
        click.echo(f"  Seeder class '{seeder_class}' not found.", err=True)
        raise SystemExit(1)

    instance = cls()
    instance.run()
    click.echo(f"  Seeded: {seeder_class}")
