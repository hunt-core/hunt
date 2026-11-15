from __future__ import annotations

import click


def _boot() -> None:
    from hunt.console.commands.queue_work import boot_application

    if not boot_application():
        click.echo("  Could not boot the application. Run this command from the application root.", err=True)
        raise SystemExit(1)


def _hash_ids() -> None:
    from hunt.session.migration import hash_session_ids

    try:
        result = hash_session_ids()
    except Exception as exc:
        click.echo(f"  Could not hash the stored session ids: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(
        f"  Hashed {result['rows']} user_sessions row(s) and moved {result['sessions']} stored session(s)."
        " Nobody was logged out."
    )


@click.command("session:prune")
@click.option(
    "--max-age",
    type=click.IntRange(min=0),
    default=None,
    help="Remove sessions idle for more than this many seconds (default: the session lifetime).",
)
@click.option(
    "--hash-ids",
    is_flag=True,
    default=False,
    help="Instead of pruning, replace session ids stored by earlier releases with their hashes (run once after upgrading).",
)
def session_prune_command(max_age: int | None, hash_ids: bool = False) -> None:
    """Delete expired rows from the user_sessions table, with their session data."""
    _boot()
    if hash_ids:
        _hash_ids()
        return

    from hunt.session.registry import SessionRegistry

    try:
        removed = SessionRegistry().prune(max_age)
    except Exception as exc:
        click.echo(f"  Could not prune sessions: {exc}", err=True)
        raise SystemExit(1) from exc
    click.echo(f"  Pruned {removed} expired session(s).")


@click.command("session:hash-ids")
def session_hash_ids_command() -> None:
    """Replace session ids stored by earlier releases with their hashes.

    Rewrites raw ids in the user_sessions table, renames session files and
    Redis keys. Idempotent; nobody is logged out. Same as
    `hunt session:prune --hash-ids`.
    """
    _boot()
    _hash_ids()
