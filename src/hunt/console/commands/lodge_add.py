from __future__ import annotations

import re
from pathlib import Path

import click

from hunt.console.commands.lodge_install import (
    _ENV_UPDATES,
    _HAS_HEALTHCHECK,
    _SERVICE_BLOCKS,
    _SERVICE_VOLUMES,
    _SERVICES,
    _set_env,
)


def _services_in_compose(content: str) -> list[str]:
    """Return the service names already present in compose.yaml."""
    found = []
    for svc in _SERVICES:
        if re.search(rf"^\s+{re.escape(svc)}:", content, re.MULTILINE):
            found.append(svc)
    return found


def _insert_service(compose: str, service: str) -> str:
    """Insert a service block before the networks: section."""
    block = _SERVICE_BLOCKS[service] + "\n"
    marker = "\nnetworks:"
    idx = compose.find(marker)
    if idx == -1:
        return compose + block
    return compose[:idx] + "\n" + block + compose[idx:]


def _add_depends_on(compose: str, service: str) -> str:
    """Add the new service to app's depends_on block, creating it if absent."""
    condition = "service_healthy" if service in _HAS_HEALTHCHECK else "service_started"
    entry = f"      {service}:\n        condition: {condition}"

    if "    depends_on:" in compose:
        return compose.replace("    depends_on:", f"    depends_on:\n{entry}", 1)

    # No depends_on block yet — insert after the volumes line under app
    return re.sub(
        r"(      - \"/app/\.venv\"\n)",
        rf"\1    depends_on:\n{entry}\n",
        compose,
        count=1,
    )


def _add_volume(compose: str, volume: str) -> str:
    """Append a named volume to the volumes: section, creating it if absent."""
    entry = f"  {volume}:\n    driver: local\n"
    if f"  {volume}:" in compose:
        return compose
    if "\nvolumes:" in compose:
        return re.sub(r"(\nvolumes:\n)", rf"\1{entry}", compose, count=1)
    return compose.rstrip("\n") + f"\n\nvolumes:\n{entry}"


@click.command("lodge:add")
@click.argument("service", type=click.Choice(list(_SERVICES), case_sensitive=False))
def lodge_add_command(service: str) -> None:
    """Add a service to an existing Lodge environment."""
    service = service.lower()
    cwd = Path.cwd()
    compose_file = cwd / "compose.yaml"

    if not compose_file.exists():
        click.echo("  No compose.yaml found. Run `hunt lodge:install` first.", err=True)
        raise SystemExit(1)

    content = compose_file.read_text()
    existing = _services_in_compose(content)

    if service in existing:
        click.echo(f"  Service '{service}' is already in compose.yaml.")
        return

    # Guard conflicting DB drivers
    if service == "pgsql" and "mysql" in existing:
        click.echo("  Cannot add pgsql — mysql is already configured.", err=True)
        raise SystemExit(1)
    if service == "mysql" and "pgsql" in existing:
        click.echo("  Cannot add mysql — pgsql is already configured.", err=True)
        raise SystemExit(1)

    content = _insert_service(content, service)
    content = _add_depends_on(content, service)

    if service in _SERVICE_VOLUMES:
        content = _add_volume(content, _SERVICE_VOLUMES[service])

    compose_file.write_text(content)
    click.echo(f"  ✓  Added '{service}' to compose.yaml")

    for env_file in (cwd / ".env", cwd / ".env.example"):
        if env_file.exists() and service in _ENV_UPDATES:
            env_content = env_file.read_text()
            for key, value in _ENV_UPDATES[service].items():
                env_content = _set_env(env_content, key, value)
            env_file.write_text(env_content)

    click.echo(f"  ✓  .env updated for {service}")
    click.echo("\n  Rebuild with: ./lodge build\n  Then restart: ./lodge up -d\n")
