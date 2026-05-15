from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from hunt.view.directives import preprocess


class ViewFactory:
    def __init__(self, views_path: Path, cache_path: Path | None = None) -> None:
        self._views_path = views_path
        self._cache_path = cache_path
        _hunt_views = Path(__file__).parent.parent / "views"
        loaders: list = [_TemplateLoader(views_path, cache_path)]
        if _hunt_views.is_dir():
            loaders.append(_TemplateLoader(_hunt_views, None))
        self._env = Environment(
            loader=ChoiceLoader(loaders),
            autoescape=select_autoescape(["html"]),
            auto_reload=True,
        )
        self._shared: dict[str, Any] = {}
        self._composers: dict[str, list] = {}

    def share(self, key: str, value: Any) -> None:
        """Inject a variable into every rendered template."""
        self._shared[key] = value

    def composer(self, view: str, callback) -> None:
        """Register a callback that runs before a specific view is rendered."""
        self._composers.setdefault(view, []).append(callback)

    def make(self, name: str, data: dict | None = None) -> View:
        path = name.replace(".", "/") + ".html"
        try:
            template = self._env.get_template(path)
        except TemplateNotFound as exc:
            raise FileNotFoundError(f"View [{name}] not found.") from exc

        context = {**self._shared, **(data or {})}

        # Run view composers
        for pattern, callbacks in self._composers.items():
            if pattern == name or pattern == "*":
                for cb in callbacks:
                    cb(context)

        # Inject CSRF token from request-bound session
        self._inject_csrf(context)
        # Inject auth user
        self._inject_auth(context)
        # Inject flash messages
        self._inject_flash(context)
        # Inject Gate.allows as can()
        self._inject_gate(context)
        # Inject app environment for @env directive
        self._inject_env(context)
        # Inject current request object
        self._inject_request(context)
        # Inject config helper
        self._inject_config(context)

        return View(template, context)

    def exists(self, name: str) -> bool:
        path = name.replace(".", "/") + ".html"
        try:
            self._env.get_template(path)
            return True
        except TemplateNotFound:
            return False

    # ------------------------------------------------------------------

    @staticmethod
    def _inject_csrf(context: dict) -> None:
        if "csrf_token" in context:
            return
        try:
            from hunt.auth.manager import _get_current_request

            session = getattr(_get_current_request(), "_session", None)
            if session:
                context["csrf_token"] = session.csrf_token()
        except Exception:
            context["csrf_token"] = ""

    @staticmethod
    def _inject_auth(context: dict) -> None:
        if "auth_user" in context:
            return
        try:
            from hunt.auth.manager import Auth

            context["auth_user"] = Auth.user()
        except Exception:
            context["auth_user"] = None

    @staticmethod
    def _inject_flash(context: dict) -> None:
        flash: dict = {}
        try:
            from hunt.auth.manager import _get_current_request

            session = getattr(_get_current_request(), "_session", None)
            if session:
                flash = session.all_flash()
        except Exception:
            pass
        if "flash" not in context:
            context["flash"] = flash
        if "errors" not in context:
            context["errors"] = flash.get("_errors", {})
        if "old" not in context:
            context["old"] = flash.get("_old_input", {})

    @staticmethod
    def _inject_gate(context: dict) -> None:
        if "can" in context:
            return
        try:
            from hunt.auth.gate import Gate

            def can(ability: str, *args: Any) -> bool:
                return Gate.allows(ability, *args)

            context["can"] = can
        except Exception:
            context["can"] = lambda *_: False

    @staticmethod
    def _inject_env(context: dict) -> None:
        if "_app_env" in context:
            return
        import os

        context["_app_env"] = os.environ.get("APP_ENV", "production")

    @staticmethod
    def _inject_request(context: dict) -> None:
        if "request" in context:
            return
        try:
            from hunt.auth.manager import _get_current_request

            req = _get_current_request()
            context["request"] = req
        except Exception:
            context["request"] = None

    @staticmethod
    def _inject_config(context: dict) -> None:
        if "config" in context:
            return
        try:
            from hunt.support.helpers import config

            context["config"] = config
        except Exception:
            context["config"] = lambda key, default=None: default


class View:
    def __init__(self, template: Any, data: dict) -> None:
        self._template = template
        self._data = data

    def with_(self, key: str, value: Any) -> View:
        self._data[key] = value
        return self

    def render(self) -> str:
        return self._template.render(**self._data)

    def __str__(self) -> str:
        return self.render()


class _TemplateLoader(FileSystemLoader):
    """Preprocesses @-directives before handing source to Jinja2."""

    def __init__(self, views_path: Path, cache_path: Path | None = None) -> None:
        super().__init__(str(views_path))
        self._cache_path = Path(cache_path) if cache_path else None
        if self._cache_path:
            self._cache_path.mkdir(parents=True, exist_ok=True)

    def get_source(self, environment: Any, template: str) -> tuple[str, str, Any]:
        raw_contents, filename, uptodate = super().get_source(environment, template)

        if self._cache_path:
            cache_key = hashlib.sha256(f"{filename}:{os.path.getmtime(filename)}".encode()).hexdigest()
            cache_file = self._cache_path / cache_key
            if cache_file.exists():
                processed = cache_file.read_text(encoding="utf-8")
            else:
                processed = preprocess(raw_contents)
                cache_file.write_text(processed, encoding="utf-8")
        else:
            processed = preprocess(raw_contents)

        return processed, filename, uptodate
