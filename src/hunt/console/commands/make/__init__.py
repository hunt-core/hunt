from pathlib import Path

from hunt.console.commands.make.controller import make_controller_command
from hunt.console.commands.make.middleware import make_middleware_command
from hunt.console.commands.make.migration import make_migration_command
from hunt.console.commands.make.model import make_model_command


def load_stub(name: str, fallback: str) -> str:
    """Load a stub template, checking the app's stubs/ directory first.

    Looks for ``<cwd>/stubs/<name>.stub`` before falling back to ``fallback``.
    This lets developers customise generated code without forking the framework.
    """
    cwd_stub = Path.cwd() / "stubs" / f"{name}.stub"
    if cwd_stub.exists():
        return cwd_stub.read_text()
    return fallback


__all__ = [
    "load_stub",
    "make_controller_command",
    "make_middleware_command",
    "make_migration_command",
    "make_model_command",
]
