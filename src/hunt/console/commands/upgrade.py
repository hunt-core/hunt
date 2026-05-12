from __future__ import annotations

from pathlib import Path

import click

from hunt.console.commands.new import (
    _MIGRATION_USERS,
    _MIGRATION_PASSWORD_RESETS,
    _MODEL_USER,
    _ADMIN_USER_RESOURCE,
    _AUTH_LOGIN_CONTROLLER,
    _AUTH_REGISTER_CONTROLLER,
    _AUTH_PASSWORD_CONTROLLER,
    _GUEST_MIDDLEWARE,
    _ROUTES_AUTH,
    _ROUTES_ADMIN,
    _VIEW_AUTH_LAYOUT,
    _VIEW_AUTH_LOGIN,
    _VIEW_AUTH_REGISTER,
    _VIEW_AUTH_FORGOT_PASSWORD,
    _VIEW_AUTH_RESET_PASSWORD,
)

# Relative paths → content for every scaffold-managed file.
# Only copied when the destination does not already exist.
_SCAFFOLD_FILES: dict[str, str] = {
    "database/migrations/0001_create_users_table.py": _MIGRATION_USERS,
    "database/migrations/0002_create_password_reset_tokens_table.py": _MIGRATION_PASSWORD_RESETS,
    "app/models/user.py": _MODEL_USER,
    "app/controllers/auth/__init__.py": "",
    "app/controllers/auth/login_controller.py": _AUTH_LOGIN_CONTROLLER,
    "app/controllers/auth/register_controller.py": _AUTH_REGISTER_CONTROLLER,
    "app/controllers/auth/password_controller.py": _AUTH_PASSWORD_CONTROLLER,
    "app/middleware/guest.py": _GUEST_MIDDLEWARE,
    "app/admin/__init__.py": "",
    "app/admin/user_resource.py": _ADMIN_USER_RESOURCE,
    "routes/auth.py": _ROUTES_AUTH,
    "routes/admin.py": _ROUTES_ADMIN,
    "resources/views/auth/layout.html": _VIEW_AUTH_LAYOUT,
    "resources/views/auth/login.html": _VIEW_AUTH_LOGIN,
    "resources/views/auth/register.html": _VIEW_AUTH_REGISTER,
    "resources/views/auth/forgot_password.html": _VIEW_AUTH_FORGOT_PASSWORD,
    "resources/views/auth/reset_password.html": _VIEW_AUTH_RESET_PASSWORD,
}

# bootstrap/app.py patches — each entry is (detection_string, patch_fn).
# patch_fn receives the file content and returns the patched content.


def _patch_auth_model(content: str) -> str:
    if "_Auth.set_model" in content or "Auth.set_model" in content:
        return content
    anchor = "# -- Router"
    if anchor not in content:
        return content
    block = (
        "# -- Auth model\n"
        "from app.models.user import User as _User\n"
        "from hunt.auth.manager import Auth as _Auth\n"
        "_Auth.set_model(_User)\n\n"
    )
    return content.replace(anchor, block + anchor, 1)


def _patch_route_import(content: str, module: str, alias: str) -> str:
    import_line = f"from routes.{module} import register as {alias}\n"
    if import_line in content or f"from routes.{module}" in content:
        return content
    lines = content.splitlines(keepends=True)
    # Insert after the last "from routes." import line.
    last = next(
        (i for i in reversed(range(len(lines))) if lines[i].startswith("from routes.")),
        None,
    )
    if last is not None:
        lines.insert(last + 1, import_line)
    return "".join(lines)


def _patch_route_call(content: str, alias: str) -> str:
    call_line = f"{alias}(router)\n"
    if call_line in content or f"{alias}(router)" in content:
        return content
    lines = content.splitlines(keepends=True)
    # Insert after the last "*_routes(router)" call line.
    last = next(
        (
            i for i in reversed(range(len(lines)))
            if lines[i].strip().endswith("_routes(router)")
        ),
        None,
    )
    if last is not None:
        lines.insert(last + 1, call_line)
    return "".join(lines)


def _patch_bootstrap(root: Path) -> list[str]:
    bootstrap = root / "bootstrap" / "app.py"
    if not bootstrap.exists():
        return []

    original = bootstrap.read_text()
    content = original

    patches: list[str] = []

    before = content
    content = _patch_auth_model(content)
    if content != before:
        patches.append("Auth.set_model(User)")

    for module, alias, label in [
        ("auth", "auth_routes", "routes/auth.py registration"),
        ("admin", "admin_routes", "routes/admin.py registration"),
    ]:
        before = content
        content = _patch_route_import(content, module, alias)
        content = _patch_route_call(content, alias)
        if content != before:
            patches.append(label)

    if content != original:
        bootstrap.write_text(content)

    return patches


@click.command("upgrade")
def upgrade_command() -> None:
    """Add missing scaffold files to an existing hunt application."""
    root = Path.cwd()

    if not (root / "bootstrap" / "app.py").exists():
        click.echo(
            "  Error: bootstrap/app.py not found. Run this command from your application root.",
            err=True,
        )
        raise SystemExit(1)

    added: list[str] = []
    skipped: list[str] = []

    for rel, content in _SCAFFOLD_FILES.items():
        dest = root / rel
        if dest.exists():
            skipped.append(rel)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        added.append(rel)
        click.echo(f"  + {rel}")

    bootstrap_patches = _patch_bootstrap(root)
    for patch in bootstrap_patches:
        click.echo(f"  ~ bootstrap/app.py — {patch}")

    click.echo("")

    if not added and not bootstrap_patches:
        click.echo("  Already up to date.")
        return

    if added:
        click.echo(f"  {len(added)} file(s) added.")
    if skipped:
        click.echo(f"  {len(skipped)} file(s) already present, not overwritten.")
    if bootstrap_patches:
        click.echo("  bootstrap/app.py patched.")

    click.echo("\n  Run hunt migrate to apply any new migrations.\n")
