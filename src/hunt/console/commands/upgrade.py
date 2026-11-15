from __future__ import annotations

import ast
import difflib
import json
import re
import sys
import tomllib
from pathlib import Path

import click

from hunt.console.commands.new import _NEW_CONFIG_FILES, _SCAFFOLD_FILES, _file_hash, framework_requirement


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
    (lock_dir / "scaffold.lock").write_text(json.dumps(lock, indent=2), encoding="utf-8")


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# bootstrap/app.py patches
# ---------------------------------------------------------------------------

_AUTH_ROUTES_LABEL = "routes/auth.py registration"
_ADMIN_ROUTES_LABEL = "routes/admin.py registration"
_AUTH_MODEL_LABEL = "Auth.set_model(User)"
_MAINTENANCE_LABEL = "MaintenanceMode middleware (makes `hunt down` take the app offline)"
_SECURE_HEADERS_LABEL = "SecureHeaders middleware (security headers and a Content-Security-Policy)"

_GLOBAL_MIDDLEWARE_RE = re.compile(r"^(?P<indent>[ \t]*)global_middleware[ \t]*=[ \t]*\[[ \t]*$", re.MULTILINE)

# A line that registers a route module: `web_routes(router)` or `routes.web.register(router)`.
_ROUTE_CALL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:\w+_routes|\w+(?:\.\w+)*\.register)\(\s*router\s*\)[ \t]*(?:#.*)?$",
    re.MULTILINE,
)


def _patch_auth_model(content: str) -> str:
    if re.search(r"\bset_model\s*\(", content):
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


def _find_route_callee(content: str, module: str) -> str | None:
    """Return the expression bootstrap uses to call routes/<module>.py's register(), if imported.

    Recognises ``from routes.<module> import register [as alias]`` (optionally
    parenthesised), ``import routes.<module> [as alias]`` and
    ``from routes import <module> [as alias]``.
    """
    mod = re.escape(module)
    m = re.search(
        rf"^[ \t]*from[ \t]+routes\.{mod}[ \t]+import[ \t]+\(?\s*register\b(?:[ \t]+as[ \t]+(\w+))?",
        content,
        re.MULTILINE,
    )
    if m:
        return m.group(1) or "register"
    m = re.search(rf"^[ \t]*import[ \t]+routes\.{mod}\b(?:[ \t]+as[ \t]+(\w+))?", content, re.MULTILINE)
    if m:
        return f"{m.group(1)}.register" if m.group(1) else f"routes.{module}.register"
    m = re.search(
        rf"^[ \t]*from[ \t]+routes[ \t]+import[ \t]+(?:[^#\n]*,[ \t]*)?{mod}\b(?:[ \t]+as[ \t]+(\w+))?",
        content,
        re.MULTILINE,
    )
    if m:
        return f"{m.group(1) or module}.register"
    return None


def _has_call(content: str, callee: str) -> bool:
    return re.search(rf"^[ \t]*{re.escape(callee)}[ \t]*\(", content, re.MULTILINE) is not None


def _route_wired(content: str, module: str) -> bool:
    callee = _find_route_callee(content, module)
    return callee is not None and _has_call(content, callee)


def _patch_route(content: str, module: str, alias: str) -> str:
    """Register routes/<module>.py in bootstrap: the import and the call are added together.

    Returns *content* unchanged when the module is already wired, or when no
    safe insertion point exists (the caller then tells the user to do it by hand).
    """
    callee = _find_route_callee(content, module)
    if callee is not None and _has_call(content, callee):
        return content

    need_import = callee is None
    if callee is None:
        callee = alias
    need_call = not _has_call(content, callee)

    lines = content.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    def _line_index(offset: int) -> int:
        return content.count("\n", 0, offset)

    if need_call:
        calls = list(_ROUTE_CALL_RE.finditer(content))
        if not calls:
            return content
        anchor = calls[-1]
        indent = anchor.group("indent")
        call_at = _line_index(anchor.start()) + 1
    else:
        # A call without its import (left by an older `hunt upgrade`): the import goes right above it.
        existing = re.search(rf"^(?P<indent>[ \t]*){re.escape(callee)}[ \t]*\(", content, re.MULTILINE)
        assert existing is not None
        indent = existing.group("indent")
        call_at = _line_index(existing.start())

    import_at: int | None = None
    import_indent = indent
    if need_import:
        import_at = call_at  # default: directly above the call, same block
        route_imports = list(
            re.finditer(r"^(?P<indent>[ \t]*)from[ \t]+routes\.\w+[ \t]+import[ \t]+[^(\n]+$", content, re.MULTILINE)
        )
        if route_imports:
            candidate = _line_index(route_imports[-1].start()) + 1
            if candidate <= call_at:
                import_at = candidate
                import_indent = route_imports[-1].group("indent")

    # Insert the later line first so the earlier index stays valid.
    if need_call:
        lines.insert(call_at, f"{indent}{callee}(router)\n")
    if need_import and import_at is not None:
        lines.insert(import_at, f"{import_indent}from routes.{module} import register as {alias}\n")
    return "".join(lines)


def _patch_global_middleware(content: str, name: str, import_line: str) -> str:
    """Put *name* first in the ``global_middleware = [`` list and import it just above.

    Returns *content* unchanged when the name is already used anywhere in the
    file, or when the list is not written in the scaffold's multi-line form
    (the caller then tells the user to add it by hand).
    """
    if re.search(rf"\b{re.escape(name)}\b", content):
        return content
    match = _GLOBAL_MIDDLEWARE_RE.search(content)
    if match is None:
        return content
    indent = match.group("indent")
    entry = f"{indent}    {name},\n"
    line_end = content.index("\n", match.end()) + 1 if "\n" in content[match.end() :] else len(content)
    patched = content[:line_end] + entry + content[line_end:]
    return patched[: match.start()] + f"{indent}{import_line}\n" + patched[match.start() :]


def _middleware_wired(content: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", content) is not None


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def _plan_bootstrap(
    original: str,
    include_auth: bool,
    secure_headers_import: str | None = None,
) -> tuple[str, list[str], list[str]]:
    """Return (patched content, applied patch labels, labels that need manual wiring)."""
    content = original
    patches: list[str] = []
    manual: list[str] = []
    original_ok = _parses(original)

    def _apply(label: str, candidate: str, wired: bool) -> None:
        nonlocal content
        if candidate == content:
            if not wired:
                manual.append(label)
            return
        if original_ok and not _parses(candidate):
            manual.append(label)
            return
        content = candidate
        patches.append(label)

    _apply(_AUTH_MODEL_LABEL, _patch_auth_model(content), True)
    if include_auth:
        _apply(_AUTH_ROUTES_LABEL, _patch_route(content, "auth", "auth_routes"), _route_wired(content, "auth"))
    _apply(_ADMIN_ROUTES_LABEL, _patch_route(content, "admin", "admin_routes"), _route_wired(content, "admin"))
    # SecureHeaders first, then MaintenanceMode: each goes to the top of the
    # list, so the final order is MaintenanceMode, SecureHeaders, <existing>.
    if secure_headers_import is not None:
        _apply(
            _SECURE_HEADERS_LABEL,
            _patch_global_middleware(content, "SecureHeaders", secure_headers_import),
            _middleware_wired(content, "SecureHeaders"),
        )
    _apply(
        _MAINTENANCE_LABEL,
        _patch_global_middleware(
            content, "MaintenanceMode", "from hunt.http.middleware.maintenance import MaintenanceMode"
        ),
        _middleware_wired(content, "MaintenanceMode"),
    )
    return content, patches, manual


def _auth_was_configured(content: str) -> bool:
    """True when bootstrap already references routes/auth.py in some form (import or call)."""
    return _find_route_callee(content, "auth") is not None or _has_call(content, "auth_routes")


# ---------------------------------------------------------------------------
# pyproject.toml: bare "hunt" dependency
# ---------------------------------------------------------------------------

# `hunt`, optional extras, optional version specifier, optional environment marker.
_BARE_HUNT_RE = re.compile(r"^\s*hunt\s*(?P<extras>\[[^\]]*\])?\s*(?P<spec>[^;]*?)\s*(?P<marker>;.*)?$", re.IGNORECASE)


def _rewrite_requirement(requirement: str) -> str | None:
    """Return the corrected requirement if *requirement* names the unrelated ``hunt`` distribution."""
    m = _BARE_HUNT_RE.match(requirement)
    if not m:
        return None
    spec = m.group("spec")
    if spec and spec[0] not in "<>=!~(":
        return None  # e.g. "hunt-framework", "hunt_x", "hunter", or a direct "hunt @ url" reference
    base = framework_requirement()
    name, _, bounds = base.partition(">=")
    fixed = name + (m.group("extras") or "") + (">=" + bounds if bounds else "")
    if m.group("marker"):
        fixed += " " + m.group("marker").strip()
    return fixed


def _project_requirements(data: dict) -> list[str]:
    project = data.get("project")
    if not isinstance(project, dict):
        return []
    found: list[str] = []
    deps = project.get("dependencies")
    if isinstance(deps, list):
        found.extend(d for d in deps if isinstance(d, str))
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                found.extend(d for d in group if isinstance(d, str))
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, list):
                found.extend(d for d in group if isinstance(d, str))
    return found


def _plan_pyproject(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (rewritten text, [(old requirement, new requirement), ...]).

    Only string items of dependency arrays are touched; ``name = "hunt"`` and
    other keys are left alone. The result is re-parsed, and the rewrite is
    dropped if anything other than the offending requirements changed.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return text, []

    replacements: dict[str, str] = {}
    for requirement in _project_requirements(data):
        fixed = _rewrite_requirement(requirement)
        if fixed is not None:
            replacements[requirement] = fixed
    if not replacements:
        return text, []

    new_text = text
    for old, new in replacements.items():
        for quote in ('"', "'"):
            literal = quote + old + quote
            # An array item: the previous non-blank character is `[` or `,`.
            pattern = re.compile(r"(?<=[\[,])(?P<gap>\s*)" + re.escape(literal))
            new_text = pattern.sub(lambda m, q=quote, n=new: m.group("gap") + q + n + q, new_text)

    try:
        new_data = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError:
        return text, []

    remaining = [r for r in _project_requirements(new_data) if _rewrite_requirement(r) is not None]
    if remaining or new_data.get("project", {}).get("name") != data.get("project", {}).get("name"):
        return text, []
    return new_text, sorted(replacements.items())


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


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


def _confirm(question: str, assume_yes: bool) -> bool:
    """Ask before editing a file the user owns. Without a terminal the answer is no."""
    if assume_yes:
        return True
    if not _is_interactive():
        return False
    return click.confirm(f"  {question}", default=False)


@click.command("upgrade")
@click.option("--dry-run", is_flag=True, help="Show what would change without writing anything.")
@click.option(
    "--yes", "--force", "-y", "assume_yes", is_flag=True, help="Apply bootstrap/pyproject patches without asking."
)
@click.option(
    "--with-auth",
    is_flag=True,
    help="Also register routes/auth.py (login, registration, password reset) in an app that has no auth routes.",
)
@click.option(
    "--with-security-headers",
    is_flag=True,
    help="Also add the SecureHeaders middleware (security headers and a Content-Security-Policy) to bootstrap/app.py.",
)
def upgrade_command(
    dry_run: bool = False,
    assume_yes: bool = False,
    with_auth: bool = False,
    with_security_headers: bool = False,
) -> None:
    """Add missing scaffold files to an existing hunt application.

    New scaffold files are added and unmodified ones are refreshed. Edits to
    files you own (bootstrap/app.py, pyproject.toml) are listed first and only
    applied after confirmation, or with --yes.
    """
    root = Path.cwd()

    if not (root / "bootstrap" / "app.py").exists():
        click.echo(
            "  Error: bootstrap/app.py not found. Run this command from your application root.",
            err=True,
        )
        raise SystemExit(1)

    tag = "[dry-run] " if dry_run else ""

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
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            updated_hashes[rel] = canonical_hash
            added.append(rel)
            click.echo(f"  {tag}+ {rel}")
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
        if not dry_run:
            dest.write_text(content, encoding="utf-8")
        updated_hashes[rel] = canonical_hash
        upgraded.append(rel)
        click.echo(f"  {tag}↑ {rel}")

    for rel, content in _NEW_CONFIG_FILES.items():
        dest = root / rel
        if not dest.exists():
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
            added.append(rel)
            click.echo(f"  {tag}+ {rel}")

    if not dry_run:
        _write_lock(root, updated_hashes)

    bootstrap_patches, bootstrap_pending = _upgrade_bootstrap(
        root, dry_run, assume_yes, with_auth, with_security_headers
    )
    dependency_fixed, dependency_pending = _upgrade_pyproject(root, dry_run, assume_yes)
    _warn_unguarded_models(root)

    click.echo("")

    if dry_run:
        click.echo("  Dry run: nothing was written.")
        return

    pending = bootstrap_pending or dependency_pending
    if not added and not upgraded and not bootstrap_patches and not dependency_fixed:
        if pending:
            click.echo("  No changes applied. Re-run with --yes to apply the patches listed above.")
        else:
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
    if dependency_fixed:
        click.echo("  pyproject.toml patched. Re-install dependencies (e.g. `uv pip install -e .`).")
    if pending:
        click.echo("  Some patches were not applied. Re-run with --yes to apply them.")

    click.echo("\n  Run hunt migrate to apply any new migrations.\n")


def _upgrade_bootstrap(
    root: Path,
    dry_run: bool,
    assume_yes: bool,
    with_auth: bool,
    with_security_headers: bool = False,
) -> tuple[list[str], bool]:
    """Plan, list and (after confirmation) apply bootstrap/app.py patches.

    Returns (applied patch labels, whether patches are still pending).
    """
    bootstrap = root / "bootstrap" / "app.py"
    original = bootstrap.read_text(encoding="utf-8")

    # Wiring auth routes into an app that never had them turns on login,
    # registration and password reset. That is never done implicitly.
    include_auth = True
    auth_optional = not _route_wired(original, "auth") and not _auth_was_configured(original)
    if auth_optional and not with_auth:
        include_auth = False
        click.echo(
            "  ? bootstrap/app.py does not register routes/auth.py. Registering it enables the login,\n"
            "    registration and password-reset routes (registration is ON unless config/auth.py\n"
            "    sets features.registration to False)."
        )
        if dry_run:
            click.echo("    Pass --with-auth to register them.")
        elif _is_interactive():
            include_auth = click.confirm("    Register the auth routes?", default=False)
        else:
            click.echo("    Skipped. Pass --with-auth to register them.")

    # A Content-Security-Policy can block scripts and styles an existing app
    # loads (inline blocks, CDNs), so SecureHeaders is only added on request.
    secure_headers_import: str | None = None
    if not _middleware_wired(original, "SecureHeaders"):
        if with_security_headers:
            if (root / "app" / "middleware" / "secure_headers.py").exists():
                secure_headers_import = "from app.middleware.secure_headers import SecureHeaders"
            else:
                secure_headers_import = "from hunt.http.middleware.secure_headers import SecureHeaders"
            click.echo(
                "  ! SecureHeaders sends a Content-Security-Policy. Check your pages afterwards: scripts or\n"
                "    styles loaded from other hosts must be allowed in app/middleware/secure_headers.py."
            )
        else:
            click.echo(
                "  ? bootstrap/app.py does not use the SecureHeaders middleware (security headers and a\n"
                "    Content-Security-Policy). Pass --with-security-headers to add it."
            )

    content, patches, manual = _plan_bootstrap(original, include_auth, secure_headers_import)

    for label in manual:
        click.echo(
            f"  ! bootstrap/app.py — {label}: no safe insertion point found; add the import and call by hand.",
            err=True,
        )

    if not patches:
        return [], False

    for patch in patches:
        click.echo(f"  {_tag(dry_run)}~ bootstrap/app.py — {patch}")
    if dry_run:
        _show_diff("bootstrap/app.py", original, content)
        return [], False

    if not _confirm(f"Apply {len(patches)} patch(es) to bootstrap/app.py?", assume_yes):
        click.echo("  bootstrap/app.py left unchanged (not confirmed).")
        return [], True

    bootstrap.write_text(content, encoding="utf-8")
    return patches, False


def _upgrade_pyproject(root: Path, dry_run: bool, assume_yes: bool) -> tuple[bool, bool]:
    """Offer to replace a bare ``hunt`` dependency. Returns (rewritten, still pending)."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False, False
    text = pyproject.read_text(encoding="utf-8")
    new_text, replacements = _plan_pyproject(text)
    if not replacements:
        try:
            stale = [r for r in _project_requirements(tomllib.loads(text)) if _rewrite_requirement(r) is not None]
        except tomllib.TOMLDecodeError:
            stale = []
        if stale:
            click.echo(
                f'  ! pyproject.toml depends on "{stale[0]}", an unrelated PyPI package. '
                f'Replace it by hand with "{framework_requirement()}".',
                err=True,
            )
        return False, False

    click.echo('  ! pyproject.toml depends on "hunt", which is an unrelated package on PyPI.')
    for old, new in replacements:
        click.echo(f'  {_tag(dry_run)}~ pyproject.toml — "{old}" -> "{new}"')
    if dry_run:
        return False, False

    if not _confirm("Rewrite the dependency in pyproject.toml?", assume_yes):
        click.echo("  pyproject.toml left unchanged (not confirmed).")
        return False, True

    pyproject.write_text(new_text, encoding="utf-8")
    return True, False


def _models_without_mass_assignment_rules(root: Path) -> list[str]:
    """Model classes under app/models that declare neither ``fillable`` nor ``guarded``.

    Models are guarded by default, so ``create()`` / ``fill()`` on these raise
    ``MassAssignmentError`` until one of the two is declared.
    """
    import ast

    models_dir = root / "app" / "models"
    if not models_dir.is_dir():
        return []
    found: list[str] = []
    for path in sorted(models_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(base, "id", getattr(base, "attr", "")) for base in node.bases}
            if "Model" not in bases:
                continue
            declared: set[str] = set()
            for stmt in node.body:
                targets = []
                if isinstance(stmt, ast.Assign):
                    targets = stmt.targets
                elif isinstance(stmt, ast.AnnAssign):
                    targets = [stmt.target]
                declared.update(target.id for target in targets if isinstance(target, ast.Name))
            if not declared & {"fillable", "guarded"}:
                found.append(f"{path.relative_to(root).as_posix()}: {node.name}")
    return found


def _warn_unguarded_models(root: Path) -> None:
    models = _models_without_mass_assignment_rules(root)
    if not models:
        return
    click.echo("")
    click.echo("  ! Models are now guarded by default. These declare neither `fillable` nor `guarded`,")
    click.echo("    so create()/fill() on them raises MassAssignmentError until you add one:")
    for entry in models:
        click.echo(f"      {entry}")


def _tag(dry_run: bool) -> str:
    return "[dry-run] " if dry_run else ""
