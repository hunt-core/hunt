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
        items.append(NavGroup("System", [NavLink("Queue", f"{self.prefix}/queue", icon=_queue_icon)]))
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
        from hunt.admin.controllers import dashboard as dash_ctrl
        from hunt.admin.controllers import queue as queue_ctrl
        from hunt.admin.controllers import resource as res_ctrl
        from hunt.admin.controllers import search as search_ctrl
        from hunt.admin.middleware.gate import AdminGate

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

            # Queue monitor
            router.get("/queue", queue_ctrl.index)
            router.post("/queue/failed/{id}/retry", queue_ctrl.retry)
            router.post("/queue/failed/{id}/delete", queue_ctrl.delete_failed)
            router.post("/queue/flush", queue_ctrl.flush)

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
