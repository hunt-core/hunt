from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from hunt.http.response import Response


@lru_cache(maxsize=1)
def _get_env() -> Environment:
    package_templates = Path(__file__).parent / "templates"
    loaders: list = []
    # App-level overrides: resources/views takes priority over package templates
    app_views = Path.cwd() / "resources" / "views"
    if app_views.is_dir():
        loaders.append(FileSystemLoader(str(app_views)))
    loaders.append(FileSystemLoader(str(package_templates)))
    env = Environment(
        loader=ChoiceLoader(loaders),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["tojson"] = lambda value, **kw: json.dumps(value, default=str, **kw)
    return env


class _Admin:
    """
    Singleton admin application.

    Usage::

        from hunt.admin import Admin

        @Admin.resource
        class PostResource(AdminResource):
            ...

        Admin.dashboard(TotalPostsMetric)
        Admin.gate(lambda request: Auth.user() and Auth.user().is_admin)
        Admin.register_to(router)
    """

    def __init__(self) -> None:
        self._resources: list[type] = []
        self._dashboard_cards: list = []
        self._tools: list[dict] = []
        self._gate: Callable | None = None
        self._nav: list | None = None
        self._router: Any = None
        self.prefix: str = "/hunt-admin"
        self.brand_name: str = "Hunt Admin"

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def resource(self, cls: type) -> type:
        """Decorator that registers an AdminResource subclass."""
        self._resources.append(cls)
        return cls

    def dashboard(self, *cards: Any) -> None:
        """Set the metric cards shown on the dashboard."""
        self._dashboard_cards = list(cards)

    def gate(self, fn: Callable) -> None:
        """Set a callable that receives the request and returns True/False."""
        self._gate = fn

    def tool(self, label: str, cls: type) -> None:
        """Register a custom tool page."""
        self._tools.append({"label": label, "cls": cls})

    def navigation(self, items: list) -> None:
        """Set a custom navigation structure for the admin sidebar."""
        self._nav = items

    def _build_nav(self) -> list:
        from hunt.admin.navigation import _DEFAULT_TOOL_ICON, NavGroup, NavLink, NavResource

        if self._nav is not None:
            return self._nav
        items: list = []
        if self._resources:
            items.append(NavGroup("Resources", [NavResource(r) for r in self._resources]))
        if self._tools:
            tool_links = [
                NavLink(
                    t["label"],
                    f"{self.prefix}/tools/{t['label'].lower().replace(' ', '-')}",
                    icon=_DEFAULT_TOOL_ICON,
                )
                for t in self._tools
            ]
            items.append(NavGroup("Tools", tool_links))
        _queue_icon = "M9 3.75H6.912a2.25 2.25 0 0 0-2.15 1.588L2.35 13.177a2.25 2.25 0 0 0-.1.661V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18v-4.162c0-.224-.034-.447-.1-.661L19.24 5.338a2.25 2.25 0 0 0-2.15-1.588H15M2.25 13.5h3.86a2.251 2.251 0 0 1 2.012 1.244l.256.512a2.252 2.252 0 0 0 2.013 1.244h3.218a2.252 2.252 0 0 0 2.013-1.244l.256-.512a2.251 2.251 0 0 1 2.012-1.244h3.860"
        _logs_icon = "M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
        _health_icon = "M9 12.75 11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 0 1-1.043 3.296 3.745 3.745 0 0 1-3.296 1.043A3.745 3.745 0 0 1 12 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 0 1-3.296-1.043 3.745 3.745 0 0 1-1.043-3.296A3.745 3.745 0 0 1 3 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 0 1 1.043-3.296 3.746 3.746 0 0 1 3.296-1.043A3.746 3.746 0 0 1 12 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 0 1 3.296 1.043 3.746 3.746 0 0 1 1.043 3.296A3.745 3.745 0 0 1 21 12Z"
        _cache_icon = "M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125"
        _schedule_icon = "M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"
        _routes_icon = "M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
        items.append(
            NavGroup(
                "System",
                [
                    NavLink("Health", f"{self.prefix}/health", icon=_health_icon),
                    NavLink("Queue", f"{self.prefix}/queue", icon=_queue_icon),
                    NavLink("Cache", f"{self.prefix}/cache", icon=_cache_icon),
                    NavLink("Schedule", f"{self.prefix}/schedule", icon=_schedule_icon),
                    NavLink("Routes", f"{self.prefix}/routes", icon=_routes_icon),
                    NavLink("Logs", f"{self.prefix}/logs", icon=_logs_icon),
                ],
            )
        )
        return items

    # ------------------------------------------------------------------
    # Resource lookup
    # ------------------------------------------------------------------

    def find_resource(self, key: str) -> type | None:
        """Find a registered AdminResource class by its slug."""
        for cls in self._resources:
            if cls.slug() == key:
                return cls
        return None

    # ------------------------------------------------------------------
    # Route registration
    # ------------------------------------------------------------------

    def register_to(self, router: Any) -> None:
        """Register all admin routes under self.prefix with AdminGate middleware."""
        from hunt.admin.controllers import action as action_ctrl
        from hunt.admin.controllers import cache_inspector as cache_ctrl
        from hunt.admin.controllers import dashboard as dash_ctrl
        from hunt.admin.controllers import health as health_ctrl
        from hunt.admin.controllers import logs as logs_ctrl
        from hunt.admin.controllers import queue as queue_ctrl
        from hunt.admin.controllers import relation_search as relation_search_ctrl
        from hunt.admin.controllers import resource as res_ctrl
        from hunt.admin.controllers import route_explorer as routes_ctrl
        from hunt.admin.controllers import schedule as schedule_ctrl
        from hunt.admin.controllers import search as search_ctrl
        from hunt.admin.controllers import static_assets as static_ctrl
        from hunt.admin.middleware.gate import AdminGate

        self._router = router

        # Static assets are public — served without authentication so the browser
        # can load CSS/JS before the session is verified.
        router.get(f"{self.prefix}/assets/{{filename:.+}}", static_ctrl.serve)

        with router.group(prefix=self.prefix, middleware=[AdminGate]):
            # Dashboard
            router.get("/", dash_ctrl.index)

            # Resource CRUD
            router.get("/resources/{resource_key}", res_ctrl.index)
            router.get("/resources/{resource_key}/create", res_ctrl.create)
            router.post("/resources/{resource_key}", res_ctrl.store)
            router.get("/resources/{resource_key}/{id}", res_ctrl.show)
            router.get("/resources/{resource_key}/{id}/edit", res_ctrl.edit)
            # Use POST with hidden _method field for update/delete (HTML form compat)
            router.post("/resources/{resource_key}/{id}", _method_router)
            router.post("/resources/{resource_key}/{id}/delete", res_ctrl.destroy)

            # Actions
            router.post("/resources/{resource_key}/actions/{action_slug}", action_ctrl.run)

            # Global search
            router.get("/search", search_ctrl.index)

            # Relation autocomplete (for searchable BelongsTo fields)
            router.get("/resources/{resource_key}/search-relation", relation_search_ctrl.search_relation)

            # Queue monitor
            router.get("/queue", queue_ctrl.index)
            router.post("/queue/failed/{id}/retry", queue_ctrl.retry)
            router.post("/queue/failed/{id}/delete", queue_ctrl.delete_failed)
            router.post("/queue/flush", queue_ctrl.flush)

            # Log viewer
            router.get("/logs", logs_ctrl.index)

            # Health
            router.get("/health", health_ctrl.index)

            # Cache inspector
            router.get("/cache", cache_ctrl.index)
            router.post("/cache/delete", cache_ctrl.delete)
            router.post("/cache/flush", cache_ctrl.flush)

            # Schedule monitor
            router.get("/schedule", schedule_ctrl.index)

            # Route explorer
            router.get("/routes", routes_ctrl.index)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self, template_name: str, context: dict, status: int = 200) -> Response:
        env = _get_env()
        template = env.get_template(template_name)
        html = template.render(**context)
        return Response(html, status=status)

    def _base_context(self, request: Any) -> dict:
        from hunt.auth.manager import Auth

        store = getattr(request, "_session", None)
        flash: dict[str, Any] = {}
        if store is not None:
            flash = store.all_flash()

        auth_user = None
        try:
            auth_user = Auth.user()
        except Exception:
            pass

        csrf_token = ""
        if store is not None:
            try:
                csrf_token = store.csrf_token()
            except Exception:
                pass

        return {
            "admin": self,
            "request": request,
            "resources": self._resources,
            "nav": self._build_nav(),
            "prefix": self.prefix,
            "flash": flash,
            "errors": flash.get("_errors", {}),
            "old": flash.get("_old_input", {}),
            "auth_user": auth_user,
            "brand_name": self.brand_name,
            "tools": self._tools,
            "csrf_token": csrf_token,
        }


def _method_router(request: Any, resource_key: str, id: str) -> Any:
    """
    Dispatch POST /resources/{key}/{id} to update or destroy based on _method field.

    HTML forms only support GET/POST. A hidden `_method` field with value "PUT",
    "PATCH", or "DELETE" signals the intended semantic method.
    """
    from hunt.admin.controllers import resource as res_ctrl

    method_override = (request.input("_method") or "").upper()
    if method_override in ("PUT", "PATCH"):
        return res_ctrl.update(request, resource_key, id)
    if method_override == "DELETE":
        return res_ctrl.destroy(request, resource_key, id)
    # Default to update if no override (plain POST to /{id})
    return res_ctrl.update(request, resource_key, id)


# Module-level singleton
Admin = _Admin()
