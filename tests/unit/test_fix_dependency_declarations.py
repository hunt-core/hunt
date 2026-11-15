"""Declared dependencies must match what the framework imports (BLD-02).

Every third-party module imported under ``src/hunt`` has to be a declared
dependency (core, or an optional extra for lazily imported integrations), and
every declared core dependency has to be used. This catches both "imported
but not declared" (breaks for users) and "declared but unused" (dead weight).
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "hunt"

# Import name -> distribution name, where they differ.
DISTRIBUTION = {
    "dotenv": "python-dotenv",
    "sentry_sdk": "sentry-sdk",
    "IPython": "ipython",
    "psycopg2": "psycopg2-binary",
    "botocore": "boto3",
}

# Modules that belong to a generated application, not to a distribution. The
# framework imports them when it runs inside an app (bootstrap, user config).
APPLICATION_MODULES = {"app", "bootstrap", "config", "routes", "database", "tests"}

# Installed as a hard dependency of a declared package and imported directly.
TRANSITIVE = {"markupsafe": "jinja2", "traitlets": "ipython"}

# Declared core dependencies that are loaded by name rather than imported:
# SQLAlchemy loads the MySQL driver from the "mysql+pymysql" URL.
LOADED_BY_NAME = {"pymysql"}


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    return _normalise(re.split(r"[\s\[<>=!~;]", requirement, maxsplit=1)[0])


def _declared() -> tuple[set[str], set[str]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = {_requirement_name(r) for r in project["dependencies"]}
    extras = {
        _requirement_name(r)
        for extra, requirements in project.get("optional-dependencies", {}).items()
        if extra != "dev"
        for r in requirements
    }
    return core, extras


def _third_party_imports() -> dict[str, set[str]]:
    """Top-level third-party module name -> files importing it."""
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".", 1)[0]
                if top == "hunt" or top in sys.stdlib_module_names or top in APPLICATION_MODULES:
                    continue
                found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_imported_package_is_declared():
    core, extras = _declared()
    undeclared = {}
    for module, files in _third_party_imports().items():
        if module in TRANSITIVE:
            parent = _normalise(TRANSITIVE[module])
            assert parent in core or parent in extras, f"{module} relies on {TRANSITIVE[module]} being declared"
            continue
        distribution = _normalise(DISTRIBUTION.get(module, module))
        if distribution not in core and distribution not in extras:
            undeclared[module] = sorted(files)
    assert not undeclared, f"Imported but not declared in pyproject.toml: {undeclared}"


def test_every_core_dependency_is_used():
    core, _ = _declared()
    imported = {_normalise(DISTRIBUTION.get(module, module)) for module in _third_party_imports()}
    unused = sorted(core - imported - {_normalise(name) for name in LOADED_BY_NAME})
    assert not unused, f"Declared in pyproject.toml but never imported under src/hunt: {unused}"


def test_optional_integrations_are_never_imported_at_module_level():
    """An extra must stay optional: importing ``hunt`` modules may not require it."""
    core, extras = _declared()
    optional_only = extras - core
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:  # module level only
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".", 1)[0]
                top = TRANSITIVE.get(top, top)
                if _normalise(DISTRIBUTION.get(top, top)) in optional_only:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders, f"Optional packages imported at module level: {offenders}"
