from __future__ import annotations

import importlib
import sys
from pathlib import Path

import click


@click.command("db:seed")
@click.option("--class", "seeder_class", default="DatabaseSeeder", help="Seeder class to run")
def db_seed_command(seeder_class: str) -> None:
    """Run database seeders."""
    from dotenv import load_dotenv
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    sys.path.insert(0, str(Path.cwd()))

    try:
        module = importlib.import_module("database.seeders")
        cls = getattr(module, seeder_class)
    except (ImportError, AttributeError):
        seed_file = Path.cwd() / "database" / "seeders.py"
        if not seed_file.exists():
            click.echo(f"  Seeder file not found: database/seeders.py", err=True)
            raise SystemExit(1)
        spec = importlib.util.spec_from_file_location("database.seeders", seed_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, seeder_class, None)
        if cls is None:
            click.echo(f"  Class '{seeder_class}' not found in database/seeders.py", err=True)
            raise SystemExit(1)

    instance = cls()
    instance.run()
    click.echo(f"  Seeded: {seeder_class}")
