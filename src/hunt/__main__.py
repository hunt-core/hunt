"""Allow ``python -m hunt`` as an alias for the ``hunt`` console script."""

from __future__ import annotations

from hunt.console.kernel import cli

if __name__ == "__main__":
    cli(prog_name="hunt")
