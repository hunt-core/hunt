from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import click


def _discover_jobs(jobs_dir: Path) -> list[dict]:
    """Walk jobs_dir, import every .py file, and return metadata for each Job subclass found."""
    from hunt.queue.job import Job

    found: list[dict] = []
    cwd = Path.cwd()

    for py_file in sorted(jobs_dir.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        try:
            rel = py_file.relative_to(cwd)
            module_path = ".".join(rel.with_suffix("").parts)
            spec = importlib.util.spec_from_file_location(module_path, py_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules.setdefault(module_path, mod)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            for attr_name in dir(mod):
                obj = getattr(mod, attr_name, None)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, Job)
                    and obj is not Job
                    and getattr(obj, "__module__", None) == module_path
                ):
                    job_name = getattr(obj, "name", "") or ""
                    found.append(
                        {
                            "cls": obj,
                            "name": job_name,
                            "class_name": attr_name,
                            "class_path": f"{module_path}.{attr_name}",
                            "queue": getattr(obj, "queue", "default"),
                            "tries": getattr(obj, "tries", 3),
                        }
                    )
        except Exception:
            pass

    return found


@click.command("job:list")
def job_list_command() -> None:
    """List all discovered Job classes in app/jobs/."""
    from dotenv import load_dotenv

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)

    sys.path.insert(0, str(Path.cwd()))

    jobs_dir = Path.cwd() / "app" / "jobs"
    if not jobs_dir.is_dir():
        click.echo("  No app/jobs/ directory found.")
        return

    jobs = _discover_jobs(jobs_dir)
    if not jobs:
        click.echo("  No jobs found in app/jobs/.")
        return

    col_name = max((len(j["name"] or j["class_name"]) for j in jobs), default=4)
    col_name = max(col_name, 4)
    col_class = max((len(j["class_path"]) for j in jobs), default=5)
    col_class = max(col_class, 5)
    col_queue = max((len(j["queue"]) for j in jobs), default=5)
    col_queue = max(col_queue, 5)

    header = f"  {'Name':<{col_name}}  {'Class':<{col_class}}  {'Queue':<{col_queue}}  Tries"
    click.echo(header)
    click.echo("  " + "-" * (len(header) - 2))

    for j in jobs:
        display_name = j["name"] or click.style(j["class_name"], dim=True)
        click.echo(
            f"  {display_name:<{col_name}}  {j['class_path']:<{col_class}}  {j['queue']:<{col_queue}}  {j['tries']}"
        )
