from __future__ import annotations

from hunt.http.request import Request
from hunt.http.response import Response


def _middleware_name(mw: object) -> str:
    if isinstance(mw, type):
        return mw.__name__
    return type(mw).__name__


def index(request: Request) -> Response:
    from hunt.admin.application import Admin

    router = getattr(Admin, "_router", None)
    search = (request.query("search") or "").strip().lower()
    method_filter = (request.query("method") or "").upper()

    rows = []
    if router is not None:
        for route in router.routes():
            methods = [m for m in route.methods if m != "HEAD"]
            if method_filter and method_filter not in methods:
                continue
            method_str = "|".join(methods)
            name = route.name or ""
            middleware = [_middleware_name(m) for m in route._middleware]
            domain = route._domain or ""
            if search and (
                search not in route.uri.lower() and search not in name.lower() and search not in method_str.lower()
            ):
                continue
            rows.append(
                {
                    "methods": methods,
                    "uri": route.uri,
                    "name": name,
                    "middleware": middleware,
                    "domain": domain,
                }
            )

    ctx = Admin._base_context(request)
    ctx.update(
        {
            "title": "Routes",
            "routes": rows,
            "search": request.query("search") or "",
            "method_filter": method_filter,
            "total": len(rows),
            "router_available": router is not None,
        }
    )
    return Admin._render("admin/routes/index.html", ctx)
