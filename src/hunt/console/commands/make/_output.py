from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click


class _OutputContext:
    """Shared output channel for make:* commands.

    Set dry_run=True to preview files without writing them.
    Set as_json=True to suppress human-readable output and emit a single JSON
    object at the end (call finish() after all writes).

    Usage inside a make helper:

        from hunt.console.commands.make._output import output
        output.write(path, content, label="Created Model")
    """

    def __init__(self) -> None:
        self.dry_run: bool = False
        self.as_json: bool = False
        self._records: list[dict[str, Any]] = []

    def configure(self, *, dry_run: bool = False, as_json: bool = False) -> None:
        self.dry_run = dry_run
        self.as_json = as_json
        self._records = []

    def write(self, path: Path, content: str, label: str = "Created") -> bool:
        """Write *content* to *path*. Returns True if the file was written."""
        try:
            rel = str(path.relative_to(Path.cwd()))
        except ValueError:
            rel = str(path)

        if not self.dry_run and path.exists():
            self._records.append({"action": "exists", "file": rel})
            if not self.as_json:
                click.echo(f"  Already exists: {rel}")
            return False

        if self.dry_run:
            self._records.append({"action": "dry_run", "file": rel})
            click.echo(f"  [dry-run] {label}: {rel}")
            return False

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._records.append({"action": "created", "file": rel})
        if not self.as_json:
            click.echo(f"  {label}: {rel}")
        return True

    def echo(self, message: str) -> None:
        """Emit a status message, suppressed in --json mode."""
        if not self.as_json:
            click.echo(message)

    def finish(self) -> None:
        """Emit JSON summary if --json mode is active. Call once per command."""
        if not self.as_json:
            return
        created = [r["file"] for r in self._records if r["action"] == "created"]
        dry = [r["file"] for r in self._records if r["action"] == "dry_run"]
        skipped = [r["file"] for r in self._records if r["action"] == "exists"]
        click.echo(json.dumps({"created": created, "dry_run": dry, "skipped": skipped}))


output = _OutputContext()
