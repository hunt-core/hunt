from __future__ import annotations

import html
import json
from pathlib import Path
from typing import ClassVar

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

_SENTINEL = ".maintenance"
_DEFAULT_MESSAGE = "We'll be back shortly. Please try again later."


def maintenance_file() -> Path:
    """Path of the maintenance sentinel: ``<application base path>/.maintenance``.

    Shared by this middleware and the ``hunt down`` / ``hunt up`` commands so
    both agree on the location whatever the process's working directory is.
    Falls back to the working directory when no application is bootstrapped.
    """
    try:
        from hunt.container import facade

        app = facade._app
        base = getattr(app, "base_path", None) if app is not None else None
        if base:
            return Path(base) / _SENTINEL
    except Exception:
        pass
    return Path.cwd() / _SENTINEL


class MaintenanceMode(Middleware):
    """Return 503 when a .maintenance file exists in the project root.

    Use `hunt down` / `hunt up` to toggle maintenance mode.

    The .maintenance file is JSON:
        {"message": "...", "retry_after": 60}

    Subclass and override ``bypass_ips`` to allow specific IPs through:

        class MyMaintenance(MaintenanceMode):
            bypass_ips = ["203.0.113.42"]
    """

    reads_body = False  # never touches the request body

    bypass_ips: ClassVar[list[str]] = []

    async def handle(self, request: Request, next: Next) -> Response:
        sentinel = maintenance_file()
        if not sentinel.exists():
            return await next(request)

        if request.ip in self.bypass_ips:
            return await next(request)

        try:
            data = json.loads(sentinel.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            # An empty file, a bare string or a list still means "down".
            data = {}

        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            message = _DEFAULT_MESSAGE
        try:
            retry_after = max(0, int(data.get("retry_after", 60)))
        except (TypeError, ValueError):
            retry_after = 60

        if request.expects_json():
            from hunt.http.response import JsonResponse

            response: Response = JsonResponse({"error": message}, 503)
        else:
            response = Response(_MAINTENANCE_HTML.replace("{{message}}", html.escape(message)), status=503)
        response.header("Retry-After", str(retry_after))
        response.header("Cache-Control", "no-store")
        return response


_MAINTENANCE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Service Unavailable</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, -apple-system, sans-serif; background: #0f0f0f; color: #e5e5e5;
       min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.wrap { text-align: center; max-width: 420px; padding: 2rem; }
h1 { font-size: 1.5rem; font-weight: 600; color: #fff; margin-bottom: .75rem; }
p { font-size: .95rem; color: #888; line-height: 1.6; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Down for Maintenance</h1>
  <p>{{message}}</p>
</div>
</body>
</html>
"""
