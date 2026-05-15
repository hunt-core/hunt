from __future__ import annotations

import click


@click.command("tinker")
def tinker_command() -> None:
    """Start an interactive REPL with the application bootstrapped."""
    try:
        import IPython
        from traitlets.config import Config

        cfg = Config()
        cfg.TerminalInteractiveShell.banner1 = "hunt Tinker — interactive REPL\nType 'exit' or press Ctrl+D to quit.\n"

        # Bootstrap app
        import os
        import sys

        sys.path.insert(0, os.getcwd())
        try:
            from bootstrap.app import application  # noqa: F401
        except ImportError:
            pass

        IPython.start_ipython(argv=[], config=cfg)
    except ImportError:
        click.echo("IPython is not installed. Run: pip install ipython")
        import code

        code.interact(banner="hunt Tinker (basic REPL)", local={})
