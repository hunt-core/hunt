from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import tempfile
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError, Undefined
from markupsafe import Markup, escape

from hunt.view.directives import PUSH_FUNCTION, STACK_FUNCTION, preprocess

# ------------------------------------------------------------------
# safe_url filter
# ------------------------------------------------------------------

SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

_URL_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):")
_URL_IGNORED_RE = re.compile(r"[\x00-\x20\x7f]+")


def safe_url(value: Any, fallback: str = "#") -> str:
    """
    Jinja filter for ``href``-like attributes: ``<a href="{{ url|safe_url }}">``.

    Relative URLs and the schemes in :data:`SAFE_URL_SCHEMES` are returned
    unchanged; anything else (``javascript:``, ``data:``, ``vbscript:`` ...)
    is replaced by *fallback*.  The result is a plain string, so it is still
    HTML-escaped by autoescaping.
    """
    if value is None or isinstance(value, Undefined):
        return ""
    # Markup is not escaped again on output, so judge it by what the browser will see.
    url = value.unescape() if isinstance(value, Markup) else str(value)
    url = url.strip()
    # Browsers drop whitespace and control characters inside the scheme ("java\tscript:").
    match = _URL_SCHEME_RE.match(_URL_IGNORED_RE.sub("", url))
    if match and match.group(1).lower() not in SAFE_URL_SCHEMES:
        return fallback
    return url


# ------------------------------------------------------------------
# @stack / @push runtime
# ------------------------------------------------------------------


class _StackCollector:
    """
    Collects ``@push`` / ``@prepend`` fragments during one render.

    ``@stack('name')`` may be rendered before the fragments are pushed (a
    ``<head>`` stack filled from a section further down), so it only emits a
    placeholder; :meth:`resolve` substitutes the collected fragments once the
    whole template has been rendered.
    """

    def __init__(self) -> None:
        self._nonce = secrets.token_hex(8)
        self._items: dict[str, list[str]] = {}
        self._placeholders = False

    def placeholder(self, name: str) -> Markup:
        self._placeholders = True
        return Markup(f"<!--hunt-stack:{self._nonce}:{name}-->")

    def push(self, name: str, content: Any, prepend: bool = False) -> None:
        fragment = str(escape(content))
        items = self._items.setdefault(name, [])
        if prepend:
            items.insert(0, fragment)
        else:
            items.append(fragment)

    def resolve(self, output: str) -> str:
        if not self._placeholders:
            return output
        pattern = re.compile(rf"<!--hunt-stack:{self._nonce}:(\w+)-->")
        return pattern.sub(lambda m: "".join(self._items.get(m.group(1), ())), output)


_active_stacks: ContextVar[_StackCollector | None] = ContextVar("hunt_view_stacks", default=None)


def _stack_global(name: str) -> Markup:
    stacks = _active_stacks.get()
    return stacks.placeholder(name) if stacks is not None else Markup("")


def _push_global(name: str, content: Any, prepend: bool = False) -> str:
    stacks = _active_stacks.get()
    if stacks is not None:
        stacks.push(name, content, prepend)
    return ""


def register_view_helpers(env: Environment) -> None:
    """Install the filters and globals that hunt templates and directives rely on."""
    env.filters.setdefault("safe_url", safe_url)
    env.globals.setdefault(STACK_FUNCTION, _stack_global)
    env.globals.setdefault(PUSH_FUNCTION, _push_global)


class _OldInput:
    """Callable + attribute-accessible wrapper for old form input data."""

    def __init__(self, data: dict) -> None:
        object.__setattr__(self, "_data", data)

    def __call__(self, key: str, default: str = "") -> Any:
        return object.__getattribute__(self, "_data").get(key, default)

    def __getitem__(self, key: str) -> Any:
        return object.__getattribute__(self, "_data").get(key, "")

    def __getattr__(self, key: str) -> Any:
        return object.__getattribute__(self, "_data").get(key, "")

    def get(self, key: str, default: Any = "") -> Any:
        return object.__getattribute__(self, "_data").get(key, default)


class ViewFactory:
    def __init__(
        self,
        views_path: Path | str | list[Path | str],
        cache_path: Path | None = None,
        extension: str = ".html",
    ) -> None:
        paths = [Path(p) for p in (views_path if isinstance(views_path, (list, tuple)) else [views_path])]
        self._views_path = paths[0]
        self._cache_path = cache_path
        self._extension = extension if extension.startswith(".") else f".{extension}"
        _hunt_views = Path(__file__).parent.parent / "views"
        loaders: list = [_TemplateLoader(p, cache_path, self._extension) for p in paths]
        if _hunt_views.is_dir():
            loaders.append(_TemplateLoader(_hunt_views, None, self._extension))
        self._env = Environment(
            loader=_ViewLoader(loaders, self._extension),
            # Always on: escaping must not depend on the configured view extension.
            autoescape=True,
            auto_reload=True,
        )
        register_view_helpers(self._env)
        self._shared: dict[str, Any] = {}
        self._composers: dict[str, list] = {}

    def share(self, key: str, value: Any) -> None:
        """Inject a variable into every rendered template."""
        self._shared[key] = value

    def composer(self, view: str, callback) -> None:
        """Register a callback that runs before a specific view is rendered."""
        self._composers.setdefault(view, []).append(callback)

    def make(self, name: str, data: dict | None = None) -> View:
        path = name.replace(".", "/") + self._extension
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
        path = name.replace(".", "/") + self._extension
        try:
            self._env.get_template(path)
            return True
        except TemplateNotFound:
            return False

    def prune_cache(self, max_age: float | None = None) -> int:
        """Delete compiled templates (all of them, or only those older than *max_age* seconds)."""
        if self._cache_path is None:
            return 0
        return prune_compiled_views(self._cache_path, max_age)

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
            context["old"] = _OldInput(flash.get("_old_input", {}))

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
        if _active_stacks.get() is not None:
            # Nested render (a view rendered while another one renders): share the outer stacks.
            return self._template.render(**self._data)
        stacks = _StackCollector()
        token = _active_stacks.set(stacks)
        try:
            output = self._template.render(**self._data)
        finally:
            _active_stacks.reset(token)
        return stacks.resolve(output)

    def __str__(self) -> str:
        return self.render()


# ------------------------------------------------------------------
# Compiled-template cache
# ------------------------------------------------------------------

_COMPILED_NAME_RE = re.compile(r"[0-9a-f]{32}-[0-9a-f]{16}")
_LEGACY_NAME_RE = re.compile(r"[0-9a-f]{64}")
_TMP_PREFIX = ".hunt-view-"


def compiled_view_cache_name(filename: str | os.PathLike, extension: str = ".html") -> str:
    """
    File name of the compiled copy of the template *filename* inside the view cache.

    ``<template id>-<state>``: the first part identifies the template file, the
    second changes with its mtime, the hunt version (the preprocessor output
    may differ between releases) and the view extension.
    """
    from hunt import __version__

    filename = os.fspath(filename)
    ident = hashlib.sha256(filename.encode()).hexdigest()[:32]
    state = f"{os.stat(filename).st_mtime_ns}:{__version__}:{extension}"
    return f"{ident}-{hashlib.sha256(state.encode()).hexdigest()[:16]}"


def write_compiled_view(cache_path: str | os.PathLike, name: str, processed: str) -> None:
    """Atomically store *processed* as *name* and drop older compiled copies of the same template."""
    cache_dir = Path(cache_path)
    fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=_TMP_PREFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(processed)
        os.replace(tmp, cache_dir / name)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    for stale in cache_dir.glob(f"{name.split('-', 1)[0]}-*"):
        if stale.name != name:
            with contextlib.suppress(OSError):
                stale.unlink()


def prune_compiled_views(cache_path: str | os.PathLike, max_age: float | None = None) -> int:
    """
    Remove compiled templates from *cache_path* and return how many were deleted.

    Only files written by the view cache are touched (current and legacy
    naming, plus temp files left by an interrupted write).  With *max_age*
    only files not modified in the last *max_age* seconds are removed.
    """
    cache_dir = Path(cache_path)
    if not cache_dir.is_dir():
        return 0
    cutoff = None if max_age is None else time.time() - max_age
    removed = 0
    for entry in cache_dir.iterdir():
        name = entry.name
        if not (_COMPILED_NAME_RE.fullmatch(name) or _LEGACY_NAME_RE.fullmatch(name) or name.startswith(_TMP_PREFIX)):
            continue
        try:
            if not entry.is_file() or (cutoff is not None and entry.stat().st_mtime > cutoff):
                continue
            entry.unlink()
            removed += 1
        except OSError:
            continue
    return removed


class _ViewLoader(ChoiceLoader):
    """
    ChoiceLoader that falls back to ``.html`` when the configured view
    extension is something else, so the templates bundled with hunt (auth
    views, components) stay reachable.
    """

    def __init__(self, loaders: list, extension: str = ".html") -> None:
        super().__init__(loaders)
        self._extension = extension

    def _fallback(self, template: str) -> str | None:
        if self._extension != ".html" and template.endswith(self._extension):
            return template[: -len(self._extension)] + ".html"
        return None

    def get_source(self, environment: Any, template: str) -> tuple[str, str | None, Any]:
        try:
            return super().get_source(environment, template)
        except TemplateNotFound:
            fallback = self._fallback(template)
            if fallback is None:
                raise
        try:
            return super().get_source(environment, fallback)
        except TemplateNotFound:
            raise TemplateNotFound(template) from None

    def load(self, environment: Any, name: str, globals: Any = None) -> Any:
        try:
            return super().load(environment, name, globals)
        except TemplateNotFound:
            fallback = self._fallback(name)
            if fallback is None:
                raise
        try:
            return super().load(environment, fallback, globals)
        except TemplateNotFound:
            raise TemplateNotFound(name) from None


class _TemplateLoader(FileSystemLoader):
    """Preprocesses @-directives before handing source to Jinja2."""

    def __init__(self, views_path: Path, cache_path: Path | None = None, extension: str = ".html") -> None:
        super().__init__(str(views_path))
        self._cache_path = Path(cache_path) if cache_path else None
        self._extension = extension
        if self._cache_path:
            self._cache_path.mkdir(parents=True, exist_ok=True)

    def _preprocess(self, source: str, template: str, filename: str) -> str:
        try:
            return preprocess(source, self._extension)
        except TemplateSyntaxError as exc:
            raise TemplateSyntaxError(exc.message or "", exc.lineno, template, filename) from None

    def get_source(self, environment: Any, template: str) -> tuple[str, str, Any]:
        raw_contents, filename, uptodate = super().get_source(environment, template)

        if not self._cache_path:
            return self._preprocess(raw_contents, template, filename), filename, uptodate

        try:
            cache_name = compiled_view_cache_name(filename, self._extension)
        except OSError:
            return self._preprocess(raw_contents, template, filename), filename, uptodate

        try:
            processed = (self._cache_path / cache_name).read_text(encoding="utf-8")
        except OSError:
            processed = self._preprocess(raw_contents, template, filename)
            # A read-only or missing cache directory must not break rendering.
            with contextlib.suppress(OSError):
                write_compiled_view(self._cache_path, cache_name, processed)

        return processed, filename, uptodate
