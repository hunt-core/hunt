from __future__ import annotations

import difflib
import json
from pathlib import Path

import click

from hunt.console.commands.new import _NEW_CONFIG_FILES, _SCAFFOLD_FILES, _file_hash


def _read_lock(root: Path) -> dict[str, str]:
    lock_file = root / ".hunt" / "scaffold.lock"
    if not lock_file.exists():
        return {}
    try:
        data = json.loads(lock_file.read_text())
        return data.get("files", {})
    except Exception:
        return {}


def _write_lock(root: Path, hashes: dict[str, str]) -> None:
    lock_dir = root / ".hunt"
    lock_dir.mkdir(exist_ok=True)
    lock = {"version": 1, "files": hashes}
    (lock_dir / "scaffold.lock").write_text(json.dumps(lock, indent=2))


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
    last = next(
        (i for i in reversed(range(len(lines))) if lines[i].strip().endswith("_routes(router)")),
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


def _show_diff(rel: str, local: str, canonical: str) -> None:
    lines = list(
        difflib.unified_diff(
            local.splitlines(keepends=True),
            canonical.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    if not lines:
        return
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("---") or line.startswith("+++"):
            click.echo(click.style(f"    {line}", fg="cyan"))
        elif line.startswith("@@"):
            click.echo(click.style(f"    {line}", fg="yellow"))
        elif line.startswith("+"):
            click.echo(click.style(f"    {line}", fg="green"))
        elif line.startswith("-"):
            click.echo(click.style(f"    {line}", fg="red"))
        else:
            click.echo(f"    {line}")


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

    stored_hashes = _read_lock(root)
    updated_hashes = dict(stored_hashes)

    added: list[str] = []
    upgraded: list[str] = []
    skipped_custom: list[str] = []
    skipped_present: list[str] = []

    for rel, content in _SCAFFOLD_FILES.items():
        dest = root / rel
        canonical_hash = _file_hash(content)

        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            updated_hashes[rel] = canonical_hash
            added.append(rel)
            click.echo(f"  + {rel}")
            continue

        current_hash = _file_hash(dest.read_text())

        if current_hash == canonical_hash:
            # File matches canonical content — already up to date.
            updated_hashes[rel] = canonical_hash
            skipped_present.append(rel)
            continue

        stored_hash = stored_hashes.get(rel)

        if stored_hash is None or current_hash != stored_hash:
            # File has been customised — skip to preserve changes, show diff.
            skipped_custom.append(rel)
            click.echo(f"  ~ {rel} (customised — skipped)")
            _show_diff(rel, dest.read_text(), content)
            continue

        # File is unmodified from the last scaffold write — safe to update.
        dest.write_text(content)
        updated_hashes[rel] = canonical_hash
        upgraded.append(rel)
        click.echo(f"  ↑ {rel}")

    for rel, content in _NEW_CONFIG_FILES.items():
        dest = root / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            added.append(rel)
            click.echo(f"  + {rel}")

    _write_lock(root, updated_hashes)

    bootstrap_patches = _patch_bootstrap(root)
    for patch in bootstrap_patches:
        click.echo(f"  ~ bootstrap/app.py — {patch}")

    click.echo("")

    if not added and not upgraded and not bootstrap_patches:
        click.echo("  Already up to date.")
        return

    if added:
        click.echo(f"  {len(added)} file(s) added.")
    if upgraded:
        click.echo(f"  {len(upgraded)} file(s) updated.")
    if skipped_custom:
        click.echo(f"  {len(skipped_custom)} customised file(s) skipped.")
    if bootstrap_patches:
        click.echo("  bootstrap/app.py patched.")

    click.echo("\n  Run hunt migrate to apply any new migrations.\n")
