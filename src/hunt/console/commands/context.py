from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

import click


@click.command("context")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON (default: markdown)")
@click.option("--no-routes", is_flag=True, help="Skip route discovery (faster, no app boot required)")
def context_command(as_json: bool, no_routes: bool) -> None:
    """Emit a compact snapshot of the current project for use with AI agents.

    Outputs routes, models, middleware, env keys, and project structure in
    either markdown (default) or JSON. Useful as context for AI coding
    assistants that need to understand the shape of an existing project.
    """
    cwd = Path.cwd()
    ctx: dict[str, Any] = {
        "framework": "hunt",
        "version": _framework_version(),
        "routes": [],
        "models": [],
        "middleware": [],
        "env_keys": [],
        "structure": {
            "controllers": [],
            "models": [],
            "migrations": [],
            "jobs": [],
            "events": [],
            "policies": [],
        },
    }

    if not no_routes:
        ctx["routes"] = _discover_routes()

    ctx["models"] = _discover_models(cwd)
    ctx["middleware"] = _discover_middleware(cwd)
    ctx["env_keys"] = _discover_env_keys(cwd)
    ctx["structure"] = _discover_structure(cwd)

    if as_json:
        click.echo(json.dumps(ctx, indent=2))
    else:
        click.echo(_render_markdown(ctx))


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def _framework_version() -> str:
    try:
        import hunt

        return hunt.__version__
    except Exception:
        return "unknown"


def _discover_routes() -> list[dict]:
    sys.path.insert(0, os.getcwd())
    try:
        from bootstrap.app import application  # type: ignore[import]

        router = application.make("router")
        out = []
        for route in router.routes():
            action = route.action
            action_name = getattr(action, "__qualname__", None) or str(action)
            mw = [m.__name__ if isinstance(m, type) else str(m) for m in route._middleware]
            out.append(
                {
                    "method": "|".join(route.methods),
                    "uri": route.uri,
                    "name": route.name or "",
                    "action": action_name,
                    "middleware": mw,
                }
            )
        return out
    except Exception as exc:
        return [{"error": f"Could not load routes: {exc}"}]


def _discover_models(cwd: Path) -> list[dict]:
    models_dir = cwd / "app" / "models"
    if not models_dir.exists():
        return []
    results = []
    for f in sorted(models_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        info = _parse_model_file(f)
        if info:
            results.append(info)
    return results


def _parse_model_file(path: Path) -> dict | None:
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Only classes that inherit from Model (directly or aliased)
        bases = [_name(b) for b in node.bases]
        if not any("Model" in b for b in bases):
            continue

        table = _class_attr_str(node, "table") or ""
        fillable = _class_attr_list(node, "fillable") or []
        hidden = _class_attr_list(node, "hidden") or []
        relations = _find_relation_methods(node)

        return {
            "class": node.name,
            "table": table,
            "fillable": fillable,
            "hidden": hidden,
            "relations": relations,
            "file": str(path.relative_to(Path.cwd())),
        }
    return None


def _discover_middleware(cwd: Path) -> list[dict]:
    mw_dir = cwd / "app" / "http" / "middleware"
    if not mw_dir.exists():
        return []
    results = []
    for f in sorted(mw_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                results.append(
                    {
                        "class": node.name,
                        "file": str(f.relative_to(cwd)),
                    }
                )
    return results


def _discover_env_keys(cwd: Path) -> list[str]:
    env_file = cwd / ".env"
    if not env_file.exists():
        env_file = cwd / ".env.example"
    if not env_file.exists():
        return []
    keys = []
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key:
                keys.append(key)
    return keys


def _discover_structure(cwd: Path) -> dict[str, list[str]]:
    def _glob(rel: str, pattern: str) -> list[str]:
        d = cwd / rel
        if not d.exists():
            return []
        return sorted(str(p.relative_to(cwd)) for p in d.glob(pattern) if not p.name.startswith("_"))

    return {
        "controllers": _glob("app/controllers", "*.py"),
        "models": _glob("app/models", "*.py"),
        "migrations": _glob("database/migrations", "*.py"),
        "jobs": _glob("app/jobs", "*.py"),
        "events": _glob("app/events", "*.py"),
        "policies": _glob("app/policies", "*.py"),
    }


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _class_attr_str(cls: ast.ClassDef, attr: str) -> str | None:
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == attr:
                    if isinstance(node.value, ast.Constant):
                        return str(node.value.value)
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == attr:
                if node.value and isinstance(node.value, ast.Constant):
                    return str(node.value.value)
    return None


def _class_attr_list(cls: ast.ClassDef, attr: str) -> list[str] | None:
    for node in cls.body:
        value = None
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == attr:
                    value = node.value
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == attr:
                value = node.value
        if value is not None and isinstance(value, ast.List):
            return [elt.value for elt in value.elts if isinstance(elt, ast.Constant)]
    return None


def _find_relation_methods(cls: ast.ClassDef) -> list[str]:
    _RELATION_CALLS = {"has_one", "has_many", "belongs_to", "belongs_to_many"}
    rels = []
    for node in ast.walk(cls):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and _name(child.func) in _RELATION_CALLS:
                    rels.append(node.name)
                    break
    return rels


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_markdown(ctx: dict) -> str:
    lines = [
        "# hunt project context",
        "",
        f"> Framework: hunt {ctx['version']}",
        "",
    ]

    # Routes
    routes = ctx.get("routes", [])
    lines += ["## Routes", ""]
    if routes and "error" not in routes[0]:
        lines.append("| Method | URI | Name | Middleware |")
        lines.append("|--------|-----|------|------------|")
        for r in routes:
            mw = ", ".join(r.get("middleware", [])) or "—"
            lines.append(f"| {r['method']} | `{r['uri']}` | {r['name'] or '—'} | {mw} |")
    elif routes:
        lines.append(f"> {routes[0].get('error', 'No routes found.')}")
    else:
        lines.append("> No routes found.")
    lines.append("")

    # Models
    lines += ["## Models", ""]
    for m in ctx.get("models", []):
        rels = ", ".join(m.get("relations", [])) or "none"
        fill = ", ".join(f"`{f}`" for f in m.get("fillable", [])) or "none"
        lines.append(f"### {m['class']}  (`{m['table']}`)")
        lines.append(f"- **fillable:** {fill}")
        if m.get("hidden"):
            lines.append(f"- **hidden:** {', '.join(f'`{h}`' for h in m['hidden'])}")
        if m.get("relations"):
            lines.append(f"- **relations:** {rels}")
        lines.append(f"- **file:** `{m['file']}`")
        lines.append("")

    # Middleware
    lines += ["## Middleware", ""]
    for mw in ctx.get("middleware", []):
        lines.append(f"- `{mw['class']}` — `{mw['file']}`")
    if not ctx.get("middleware"):
        lines.append("- none")
    lines.append("")

    # Structure
    lines += ["## Project structure", ""]
    struct = ctx.get("structure", {})
    for group, files in struct.items():
        if files:
            lines.append(f"**{group}:** " + ", ".join(f"`{f}`" for f in files))
    lines.append("")

    # Env keys
    lines += ["## Environment keys", ""]
    keys = ctx.get("env_keys", [])
    if keys:
        lines.append(", ".join(f"`{k}`" for k in keys))
    else:
        lines.append("— no .env found")
    lines.append("")

    return "\n".join(lines)
