from __future__ import annotations

import shutil
from pathlib import Path

import click

_TAGS: dict[str, tuple[Path, Path]] = {}

_AVAILABLE_TAGS = "views, views:auth, views:components, components"


def _views_src() -> Path:
    return Path(__file__).parent.parent.parent / "views"


def _components_src() -> Path:
    return _views_src() / "components"


@click.command("vendor:publish")
@click.option("--tag", default=None, metavar="TAG", help="Publish a specific asset group (e.g. views, views:auth, components).")
@click.option("--force", is_flag=True, help="Overwrite existing files.")
def vendor_publish_command(tag: str | None, force: bool) -> None:
    """Publish framework assets into the application for customisation."""
    if tag is None or tag == "views":
        _publish_dir(_views_src(), Path.cwd() / "resources" / "views", "views", force)
    elif tag == "views:auth":
        _publish_dir(_views_src() / "auth", Path.cwd() / "resources" / "views" / "auth", "views/auth", force)
    elif tag in ("views:components", "components"):
        _publish_dir(_components_src(), Path.cwd() / "resources" / "views" / "components", "views/components", force)
    else:
        click.echo(f"  Unknown tag '{tag}'. Available tags: {_AVAILABLE_TAGS}", err=True)
        raise SystemExit(1)


def _publish_dir(src: Path, dest: Path, label: str, force: bool) -> None:
    if not src.is_dir():
        click.echo(f"  Error: built-in {label} directory not found.", err=True)
        raise SystemExit(1)

    # Fallback root used when the primary destination already has a user-edited copy.
    fallback_root = Path.cwd() / "resources" / "views" / "framework"

    copied = 0
    deferred = 0
    for src_file in sorted(src.rglob("*")):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(src)
        dest_file = dest / rel

        if dest_file.exists() and not force:
            # User already has this view — place framework copy under resources/views/framework/
            # so they can reference it without losing their customisation.
            fallback_file = fallback_root / rel
            fallback_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, fallback_file)
            click.echo(f"  Deferred: resources/views/framework/{rel}  (resources/{label}/{rel} already exists)")
            deferred += 1
            continue

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        click.echo(f"  Published: resources/{label}/{rel}")
        copied += 1

    click.echo(f"\n  {copied} file(s) published to resources/{label}/")
    if deferred:
        click.echo(f"  {deferred} file(s) already customised — framework copies placed in resources/views/framework/.")
