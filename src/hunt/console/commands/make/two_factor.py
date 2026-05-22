from __future__ import annotations

from pathlib import Path

import click


@click.command("make:2fa-controllers")
def make_two_factor_command() -> None:
    """Scaffold two-factor authentication controllers, templates, and routes."""
    cwd = Path.cwd()
    _write_controllers(cwd)
    _write_templates(cwd)
    _write_routes_snippet(cwd)
    _write_migration(cwd)
    click.echo("\n  Two-factor authentication scaffolded.")
    click.echo("  Next steps:")
    click.echo("    1. Run the generated migration: hunt migrate:run")
    click.echo("    2. Add the routes from routes/two_factor.py into routes/web.py")
    click.echo("    3. Add EnsureTwoFactorAuthenticated to your global middleware if desired")


def _write_controllers(cwd: Path) -> None:
    dest = cwd / "app" / "controllers" / "two_factor_controller.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_CONTROLLER_STUB)
    click.echo(f"  Created Controller: {dest.relative_to(cwd)}")


def _write_templates(cwd: Path) -> None:
    src = Path(__file__).parent.parent.parent / "views" / "auth" / "two_factor"
    templates_dir = cwd / "resources" / "views" / "auth" / "two_factor"
    templates_dir.mkdir(parents=True, exist_ok=True)
    for html_file in sorted(src.glob("*.html")):
        dest = templates_dir / html_file.name
        dest.write_bytes(html_file.read_bytes())
        click.echo(f"  Created Template:   {dest.relative_to(cwd)}")


def _write_routes_snippet(cwd: Path) -> None:
    dest = cwd / "routes" / "two_factor.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_ROUTES_STUB)
    click.echo(f"  Created Routes:     {dest.relative_to(cwd)}")


def _write_migration(cwd: Path) -> None:
    import time

    stamp = str(int(time.time()))
    dest = cwd / "database" / "migrations" / f"{stamp}_add_two_factor_to_users_table.py"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_MIGRATION_STUB)
    click.echo(f"  Created Migration:  {dest.relative_to(cwd)}")


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_CONTROLLER_STUB = """\
from hunt.auth.controllers.two_factor import (
    TwoFactorChallengeController,
    TwoFactorManageController,
    TwoFactorSetupController,
)

__all__ = [
    "TwoFactorSetupController",
    "TwoFactorChallengeController",
    "TwoFactorManageController",
]
"""

_ROUTES_STUB = """\
from hunt.http.router import Router
from hunt.auth.controllers.two_factor import (
    TwoFactorChallengeController,
    TwoFactorManageController,
    TwoFactorSetupController,
)


def register(router: Router) -> None:
    # 2FA setup flow (requires authenticated user)
    router.get("/two-factor/setup", TwoFactorSetupController().show).named("two-factor.setup")
    router.post("/two-factor/setup", TwoFactorSetupController().store).named("two-factor.setup.store")
    router.get("/two-factor/confirm", TwoFactorSetupController().show)
    router.post("/two-factor/confirm", TwoFactorSetupController().confirm).named("two-factor.confirm")
    router.delete("/two-factor", TwoFactorSetupController().destroy).named("two-factor.destroy")
    router.post("/two-factor/destroy", TwoFactorSetupController().destroy)

    # 2FA login challenge
    router.get("/two-factor/challenge", TwoFactorChallengeController().show).named("two-factor.challenge")
    router.post("/two-factor/challenge", TwoFactorChallengeController().store)

    # 2FA management (recovery codes)
    router.get("/two-factor/manage", TwoFactorManageController().show).named("two-factor.manage")
    router.post("/two-factor/regenerate", TwoFactorManageController().regenerate).named("two-factor.regenerate")
"""

_MIGRATION_STUB = """\
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class AddTwoFactorToUsersTable(Migration):
    def up(self) -> None:
        Schema.table("users", lambda bp: [
            bp.text("two_factor_secret").nullable(),
            bp.boolean("two_factor_enabled").default(False),
            bp.text("two_factor_recovery_codes").nullable(),
        ])

    def down(self) -> None:
        Schema.table("users", lambda bp: [
            bp.drop_column("two_factor_secret"),
            bp.drop_column("two_factor_enabled"),
            bp.drop_column("two_factor_recovery_codes"),
        ])
"""
