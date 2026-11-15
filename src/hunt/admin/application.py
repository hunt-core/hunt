from __future__ import annotations

import functools
import json
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape
from jinja2.utils import htmlsafe_json_dumps

from hunt.http.response import HttpException, Response

# System tools and whether they are reachable when the application has not
# said anything about them. The ones that expose or destroy operational data
# (sessions, cache contents, log lines, queued jobs) are opt-in.
_TOOL_DEFAULTS: dict[str, bool] = {
    "media": True,
    "health": True,
    "schedule": True,
    "routes": True,
    "sessions": False,
    "cache": False,
    "logs": False,
    "queue": False,
}


def _tool_slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")


def _tojson(value: Any, **kw: Any) -> Any:
    """``tojson`` that is safe inside ``<script>`` (escapes ``<``, ``>``, ``&``, ``'``) and tolerates dates."""
    return htmlsafe_json_dumps(value, dumps=functools.partial(json.dumps, default=str), **kw)


def _qs(base: Any = None, **overrides: Any) -> str:
    """Build a URL-encoded query string from ``base`` with ``overrides`` applied.

    ``None`` and empty-string values are dropped, so ``qs(q, page=None)``
    removes a parameter. The result is plain text: autoescaping turns ``&``
    into ``&amp;`` when it is written into an ``href``.
    """
    params: dict[str, Any] = {}
    if isinstance(base, dict):
        params.update(base)
    params.update(overrides)
    pairs = [(str(k), str(v)) for k, v in params.items() if v is not None and v != ""]
    return urlencode(pairs)


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
    env.filters["tojson"] = _tojson
    env.globals["qs"] = _qs
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

    Admin pages are sent with a Content-Security-Policy whose ``script-src``
    is ``'self'``: inline ``<script>`` blocks and inline event handlers do not
    run. Templates published with ``hunt admin:publish`` before that change
    still contain inline scripts; re-publish them (``--force``) or, as a
    stop-gap, set ``Admin.csp_allow_inline_scripts = True``.
    """

    def __init__(self) -> None:
        self._resources: list[type] = []
        self._dashboard_cards: list = []
        self._tools: list[dict] = []
        self._gate: Callable | None = None
        self._tool_rules: dict[str, Any] = {}
        self._tools_gate: Callable | None = None
        self._nav: list | None = None
        self._router: Any = None
        self.prefix: str = "/hunt-admin"
        self.brand_name: str = "Hunt Admin"
        self.media_disk: str = "public"
        self.media_path: str = "media"
        self.media_max_kb: int = 10240  # per-file upload cap for the media manager
        # Raise (fail the request) when an audit row cannot be written.
        self.audit_strict: bool = False
        # Record system-tool operations (queue, cache, media, sessions) in the audit log.
        self.audit_tools: bool = True
        # The admin Content-Security-Policy does not allow inline <script>
        # blocks or inline event handlers (onclick=...). The bundled templates
        # need neither. Set this to True only while an application still has
        # admin templates published before that change (resources/views/admin)
        # that rely on inline scripts; it restores 'unsafe-inline' for scripts.
        self.csp_allow_inline_scripts: bool = False

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

    def tool(self, label: str, cls: Any) -> None:
        """Register a custom tool page, served at ``<prefix>/tools/<slug>``.

        ``cls`` is a class (instantiated per request) or a callable. The page
        is produced by its ``index(request)``, ``render(request)`` or
        ``handle(request)`` method, or by calling it with the request.
        """
        self._tools.append({"label": label, "cls": cls, "slug": _tool_slug(label)})

    # ------------------------------------------------------------------
    # Tool authorization
    # ------------------------------------------------------------------

    def authorize_tool(self, name: str, fn: Any = True) -> None:
        """Decide who may use a system tool, beyond passing the gate.

        ``name`` is one of ``media``, ``sessions``, ``health``, ``queue``,
        ``cache``, ``schedule``, ``routes``, ``logs`` (or a custom tool slug).
        ``fn`` is a callable receiving the request and returning ``True`` to
        allow, or a plain bool. ``sessions``, ``cache``, ``logs`` and ``queue``
        are denied until enabled here (or through ``tools_gate``)::

            Admin.authorize_tool("queue", lambda request: Auth.user().role == "ops")
            Admin.authorize_tool("logs")          # everyone who passes the gate
        """
        self._tool_rules[str(name).lower()] = fn

    def tools_gate(self, fn: Callable) -> None:
        """Set one hook ``fn(request, tool_name) -> bool`` for every tool without its own rule."""
        self._tools_gate = fn

    def tool_allowed(self, request: Any, name: str) -> bool:
        """Return True when the current request may use the named tool. Fails closed."""
        from hunt.admin.authorization import _allow

        key = str(name).lower()
        try:
            if key in self._tool_rules:
                rule = self._tool_rules[key]
                result = rule(request) if callable(rule) else rule
            elif self._tools_gate is not None:
                result = self._tools_gate(request, key)
            elif key in _TOOL_DEFAULTS:
                result = _TOOL_DEFAULTS[key]
            else:
                # Custom tools registered with Admin.tool() are deliberate.
                result = any(t.get("slug") == key for t in self._tools)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Admin tool rule for %r raised; denying access", key)
            return False
        return _allow(result)

    def require_tool(self, request: Any, name: str) -> None:
        """Raise 403 unless the request may use the named tool."""
        if not self.tool_allowed(request, name):
            raise HttpException(403, "Forbidden.")

    def navigation(self, items: list) -> None:
        """Set a custom navigation structure for the admin sidebar."""
        self._nav = items

    def _build_nav(self, request: Any = None) -> list:
        """Build the sidebar. With a request, tools the user may not open are left out."""
        from hunt.admin.navigation import _DEFAULT_TOOL_ICON, NavGroup, NavLink, NavResource

        def _visible(tool: str) -> bool:
            return request is None or self.tool_allowed(request, tool)

        if self._nav is not None:
            items = list(self._nav)
        else:
            items = []
            if self._resources:
                items.append(NavGroup("Resources", [NavResource(r) for r in self._resources]))
        if self._nav is None and self._tools:
            tool_links = [
                NavLink(
                    t["label"],
                    f"{self.prefix}/tools/{t.get('slug') or _tool_slug(t['label'])}",
                    icon=_DEFAULT_TOOL_ICON,
                )
                for t in self._tools
                if _visible(t.get("slug") or _tool_slug(t["label"]))
            ]
            if tool_links:
                items.append(NavGroup("Tools", tool_links))
        _sessions_icon = "M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
        _queue_icon = "M9 3.75H6.912a2.25 2.25 0 0 0-2.15 1.588L2.35 13.177a2.25 2.25 0 0 0-.1.661V18a2.25 2.25 0 0 0 2.25 2.25h15A2.25 2.25 0 0 0 21.75 18v-4.162c0-.224-.034-.447-.1-.661L19.24 5.338a2.25 2.25 0 0 0-2.15-1.588H15M2.25 13.5h3.86a2.251 2.251 0 0 1 2.012 1.244l.256.512a2.252 2.252 0 0 0 2.013 1.244h3.218a2.252 2.252 0 0 0 2.013-1.244l.256-.512a2.251 2.251 0 0 1 2.012-1.244h3.860"
        _logs_icon = "M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
        _health_icon = "M9 12.75 11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 0 1-1.043 3.296 3.745 3.745 0 0 1-3.296 1.043A3.745 3.745 0 0 1 12 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 0 1-3.296-1.043 3.745 3.745 0 0 1-1.043-3.296A3.745 3.745 0 0 1 3 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 0 1 1.043-3.296 3.746 3.746 0 0 1 3.296-1.043A3.746 3.746 0 0 1 12 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 0 1 3.296 1.043 3.746 3.746 0 0 1 1.043 3.296A3.745 3.745 0 0 1 21 12Z"
        _cache_icon = "M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125m16.5 2.813c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125"
        _schedule_icon = "M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5"
        _routes_icon = "M13.5 16.875h3.375m0 0h3.375m-3.375 0V13.5m0 3.375v3.375M6 10.5h2.25a2.25 2.25 0 0 0 2.25-2.25V6a2.25 2.25 0 0 0-2.25-2.25H6A2.25 2.25 0 0 0 3.75 6v2.25A2.25 2.25 0 0 0 6 10.5Zm0 9.75h2.25A2.25 2.25 0 0 0 10.5 18v-2.25a2.25 2.25 0 0 0-2.25-2.25H6a2.25 2.25 0 0 0-2.25 2.25V18A2.25 2.25 0 0 0 6 20.25Zm9.75-9.75H18a2.25 2.25 0 0 0 2.25-2.25V6A2.25 2.25 0 0 0 18 3.75h-2.25A2.25 2.25 0 0 0 13.5 6v2.25a2.25 2.25 0 0 0 2.25 2.25Z"
        _media_icon = "m2.25 15.75 5.159-5.159a2.25 2.25 0 0 1 3.182 0l5.159 5.159m-1.5-1.5 1.409-1.409a2.25 2.25 0 0 1 3.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 0 0 1.5-1.5V6a1.5 1.5 0 0 0-1.5-1.5H3.75A1.5 1.5 0 0 0 2.25 6v12a1.5 1.5 0 0 0 1.5 1.5Zm10.5-11.25h.008v.008h-.008V8.25Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"
        system_links = [
            ("media", NavLink("Media", f"{self.prefix}/media", icon=_media_icon)),
            ("sessions", NavLink("Sessions", f"{self.prefix}/sessions", icon=_sessions_icon)),
            ("health", NavLink("Health", f"{self.prefix}/health", icon=_health_icon)),
            ("queue", NavLink("Queue", f"{self.prefix}/queue", icon=_queue_icon)),
            ("cache", NavLink("Cache", f"{self.prefix}/cache", icon=_cache_icon)),
            ("schedule", NavLink("Schedule", f"{self.prefix}/schedule", icon=_schedule_icon)),
            ("routes", NavLink("Routes", f"{self.prefix}/routes", icon=_routes_icon)),
            ("logs", NavLink("Logs", f"{self.prefix}/logs", icon=_logs_icon)),
        ]
        visible_links = [link for tool, link in system_links if _visible(tool)]
        if visible_links or request is None:
            items.append(NavGroup("System", visible_links))
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
        """Register all admin routes under self.prefix, behind AdminGate and CSRF verification."""
        from hunt.admin.controllers import action as action_ctrl
        from hunt.admin.controllers import cache_inspector as cache_ctrl
        from hunt.admin.controllers import dashboard as dash_ctrl
        from hunt.admin.controllers import health as health_ctrl
        from hunt.admin.controllers import logs as logs_ctrl
        from hunt.admin.controllers import media as media_ctrl
        from hunt.admin.controllers import queue as queue_ctrl
        from hunt.admin.controllers import relation_search as relation_search_ctrl
        from hunt.admin.controllers import resource as res_ctrl
        from hunt.admin.controllers import route_explorer as routes_ctrl
        from hunt.admin.controllers import schedule as schedule_ctrl
        from hunt.admin.controllers import search as search_ctrl
        from hunt.admin.controllers import sessions as sessions_ctrl
        from hunt.admin.controllers import static_assets as static_ctrl
        from hunt.admin.middleware.csrf import AdminVerifyCsrfToken
        from hunt.admin.middleware.gate import AdminGate

        self._router = router

        # Static assets are public — served without authentication so the browser
        # can load CSS/JS before the session is verified.
        router.get(f"{self.prefix}/assets/{{filename:.+}}", static_ctrl.serve)

        # AdminVerifyCsrfToken makes every state-changing admin route CSRF
        # protected even when the application has no global CSRF middleware.
        with router.group(prefix=self.prefix, middleware=[AdminGate, AdminVerifyCsrfToken]):
            # Dashboard
            router.get("/", dash_ctrl.index)

            # Resource collection routes. The router is first-match, so every
            # route with a literal segment after {resource_key} must be
            # registered before the {id} routes below, or it is shadowed.
            router.get("/resources/{resource_key}", res_ctrl.index)
            router.get("/resources/{resource_key}/create", res_ctrl.create)
            # Relation autocomplete (searchable BelongsTo / BelongsToMany pickers)
            router.get("/resources/{resource_key}/search-relation", relation_search_ctrl.search_relation)
            router.post("/resources/{resource_key}", res_ctrl.store)
            # Bulk actions
            router.post("/resources/{resource_key}/actions/{action_slug}", action_ctrl.run)

            # Resource record routes
            router.get("/resources/{resource_key}/{id}", res_ctrl.show)
            router.get("/resources/{resource_key}/{id}/edit", res_ctrl.edit)
            # HTML forms POST with a hidden _method field. Kernels that apply
            # the method override route those as PUT/PATCH/DELETE; otherwise
            # the POST lands on _method_router, which dispatches on the field.
            router.post("/resources/{resource_key}/{id}", _method_router)
            router.match(["PUT", "PATCH"], "/resources/{resource_key}/{id}", res_ctrl.update)
            router.delete("/resources/{resource_key}/{id}", res_ctrl.destroy)
            router.post("/resources/{resource_key}/{id}/delete", res_ctrl.destroy)
            router.post("/resources/{resource_key}/{id}/field-toggle", res_ctrl.toggle_field)

            # Global search
            router.get("/search", search_ctrl.index)

            # Queue monitor
            router.get("/queue", queue_ctrl.index)
            router.post("/queue/flush", queue_ctrl.flush)
            router.post("/queue/failed/{id}/retry", queue_ctrl.retry)
            router.post("/queue/failed/{id}/delete", queue_ctrl.delete_failed)

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

            # Media manager
            router.get("/media", media_ctrl.index)
            router.get("/media/api", media_ctrl.api_list)
            router.post("/media/upload", media_ctrl.upload)
            router.post("/media/delete", media_ctrl.delete)

            # Sessions manager. Sessions are addressed by their non-secret
            # public id; the raw session id never appears in a URL.
            router.get("/sessions", sessions_ctrl.index)
            router.post("/sessions/user/{user_id}/revoke", sessions_ctrl.revoke_user)
            router.post("/sessions/{public_id}/revoke", sessions_ctrl.revoke)

            # Custom tool pages registered with Admin.tool()
            router.match(["GET", "HEAD", "POST"], "/tools/{tool_slug}", _tool_router)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    # CSP for admin pages. Scripts come only from the admin's own asset route
    # (static/admin.js and friends): no inline <script>, no inline event
    # handlers, no eval. Inline styles are still used by the templates and the
    # editors; img-src allows any origin so storage images (S3, CDN, local)
    # always render.
    _ADMIN_CSP_TEMPLATE = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src {script_src}; "
        "img-src * data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'"
    )
    _ADMIN_CSP = _ADMIN_CSP_TEMPLATE.format(script_src="'self'")
    _ADMIN_CSP_INLINE_SCRIPTS = _ADMIN_CSP_TEMPLATE.format(script_src="'self' 'unsafe-inline'")

    def content_security_policy(self) -> str:
        """The Content-Security-Policy header sent with every admin page."""
        if self.csp_allow_inline_scripts is True:
            return self._ADMIN_CSP_INLINE_SCRIPTS
        return self._ADMIN_CSP

    def render(self, request: Any, template_name: str, context: dict | None = None, status: int = 200) -> Response:
        """Render a template inside the admin layout, for custom tool pages.

        The template receives the standard admin context (navigation, flash
        messages, the current user, ``prefix``) plus *context*, and the
        response carries the admin Content-Security-Policy::

            def reports(request):
                return Admin.render(request, "admin/reports.html", {"title": "Reports", "rows": rows})
        """
        data = self._base_context(request)
        data.update(context or {})
        return self._render(template_name, data, status=status)

    def _render(self, template_name: str, context: dict, status: int = 200) -> Response:
        env = _get_env()
        template = env.get_template(template_name)
        html = template.render(**context)
        resp = Response(html, status=status)
        resp.header("Content-Security-Policy", self.content_security_policy())
        return resp

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
            "nav": self._build_nav(request),
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


def _tool_router(request: Any, tool_slug: str) -> Any:
    """Serve a custom tool page registered with ``Admin.tool(label, cls)``."""
    entry = next((t for t in Admin._tools if (t.get("slug") or _tool_slug(t["label"])) == tool_slug), None)
    if entry is None:
        raise HttpException(404, "Tool not found.")
    Admin.require_tool(request, tool_slug)

    target = entry["cls"]
    instance = target() if isinstance(target, type) else target
    for method_name in ("index", "render", "handle"):
        method = getattr(instance, method_name, None)
        if callable(method):
            return method(request)
    if callable(instance):
        return instance(request)
    raise HttpException(404, "Tool not found.")


# Module-level singleton
Admin = _Admin()
