from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hunt.http.response import Response


@lru_cache(maxsize=1)
def _get_env() -> Environment:
    templates_dir = Path(__file__).parent / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
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
        from hunt.admin.middleware.gate import AdminGate
        from hunt.admin.controllers import dashboard as dash_ctrl
        from hunt.admin.controllers import resource as res_ctrl
        from hunt.admin.controllers import action as action_ctrl
        from hunt.admin.controllers import search as search_ctrl

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
            "prefix": self.prefix,
            "flash": flash,
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
