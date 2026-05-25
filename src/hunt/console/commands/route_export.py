from __future__ import annotations

import inspect
import json
import os
import re
import sys
from pathlib import Path

import click

_PATH_PARAM_RE = re.compile(r"\{(\w+)\}")


def _openapi_path(uri: str) -> str:
    return uri if uri.startswith("/") else f"/{uri}"


def _path_params(uri: str) -> list[dict]:
    return [
        {"name": m.group(1), "in": "path", "required": True, "schema": {"type": "string"}}
        for m in _PATH_PARAM_RE.finditer(uri)
    ]


def _docstring(action: object) -> tuple[str, str]:
    doc = inspect.getdoc(action) or ""
    if not doc:
        return "", ""
    lines = doc.splitlines()
    summary = lines[0].strip()
    description = "\n".join(lines[2:]).strip() if len(lines) > 2 else ""
    return summary, description


def _build_spec(router: object, title: str, version: str) -> dict:
    paths: dict = {}
    for route in router.routes():  # type: ignore[attr-defined]
        path = _openapi_path(route.uri)
        params = _path_params(route.uri)
        summary, description = _docstring(route.action)
        for method in route.methods:
            if method == "HEAD":
                continue
            operation: dict = {}
            if route.name:
                operation["operationId"] = route.name
            if summary:
                operation["summary"] = summary
            if description:
                operation["description"] = description
            if params:
                operation["parameters"] = params
            if method in ("POST", "PUT", "PATCH"):
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            operation["responses"] = {"200": {"description": "OK"}}
            paths.setdefault(path, {})[method.lower()] = operation
    return {"openapi": "3.1.0", "info": {"title": title, "version": version}, "paths": paths}


@click.command("route:export")
@click.option("--format", "fmt", default="openapi", type=click.Choice(["openapi"]), help="Export format")
@click.option("--output", "-o", default=None, help="Output file (default: stdout)")
@click.option("--title", default="API", show_default=True, help="API title")
@click.option("--version", "api_version", default="1.0.0", show_default=True, help="API version")
def route_export_command(fmt: str, output: str | None, title: str, api_version: str) -> None:
    """Export route definitions as an OpenAPI 3.1 spec."""
    sys.path.insert(0, os.getcwd())
    try:
        from bootstrap.app import application

        router = application.make("router")
    except Exception as e:
        click.echo(f"Could not load routes: {e}", err=True)
        return

    spec = _build_spec(router, title, api_version)
    content = json.dumps(spec, indent=2)

    if output:
        Path(output).write_text(content)
        click.echo(f"  Exported {len(router.routes())} routes to {output}")
    else:
        click.echo(content)
