from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.application import Application

_app_instance: Application | None = None


def _set_app(app: Application) -> None:
    global _app_instance
    _app_instance = app


def _get_app() -> Application:
    if _app_instance is None:
        raise RuntimeError("Application has not been bootstrapped")
    return _app_instance


def app(abstract: str | type | None = None, params: dict | None = None) -> Any:
    instance = _get_app()
    if abstract is None:
        return instance
    return instance.make(abstract, params)


def config(key: str, default: Any = None) -> Any:
    return _get_app().config.get(key, default)


_CANONICAL_INT_RE = re.compile(r"0|-?[1-9][0-9]*")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


def env(key: str, default: Any = None) -> Any:
    """Read an environment variable with minimal, lossless coercion.

    ``true``/``false`` become booleans, ``null`` becomes ``None``, ``(empty)`` becomes
    the empty string and canonical integers (``42``, ``-7``) become ``int``. Everything
    else is returned as the exact string: ``0123`` (a password or zip code) stays
    ``"0123"``, and ``1e5`` or ``1.5`` stay strings. Use :func:`env_int`,
    :func:`env_float` or :func:`env_bool` when a specific type is required.
    """
    val = os.environ.get(key)
    if val is None:
        return default
    low = val.lower()
    if low in ("true", "(true)"):
        return True
    if low in ("false", "(false)"):
        return False
    if low in ("null", "(null)"):
        return None
    if low == "(empty)":
        return ""
    if _CANONICAL_INT_RE.fullmatch(val):
        return int(val)
    return val


def env_int(key: str, default: int | None = None) -> int | None:
    """Read an integer environment variable; ``default`` when unset, empty or not an integer."""
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


def env_float(key: str, default: float | None = None) -> float | None:
    """Read a float environment variable; ``default`` when unset, empty or not a number."""
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val.strip())
    except ValueError:
        return default


def env_bool(key: str, default: bool = False) -> bool:
    """Read a boolean environment variable (``1/true/yes/on`` and ``0/false/no/off``)."""
    val = os.environ.get(key)
    if val is None:
        return default
    low = val.strip().lower()
    if low in _TRUE_VALUES:
        return True
    if low in _FALSE_VALUES:
        return False
    return default


def dump(*values: Any) -> Any:
    import pprint

    for v in values:
        pprint.pprint(v)
    return values[-1] if values else None


class DumpAndDie(Exception):
    """Raised by :func:`dd` while a request is being handled.

    ``SystemExit`` must not be raised inside a server worker: it would terminate the
    worker (or be swallowed by a thread pool) instead of ending just this request.
    """

    def __init__(self, values: tuple) -> None:
        self.values = values
        super().__init__("dd() called")


def dd(*values: Any) -> None:
    """Dump the values and stop: aborts the current request, or exits a CLI script."""
    import pprint

    for v in values:
        pprint.pprint(v)
    from hunt.auth.manager import _get_current_request

    if _get_current_request() is not None:
        raise DumpAndDie(values)
    raise SystemExit(0)


def base_path(*parts: str) -> str:
    return str(_get_app().path(*parts))


def app_path(*parts: str) -> str:
    return str(_get_app().app_path(*parts))


def database_path(*parts: str) -> str:
    return str(_get_app().database_path(*parts))


def resource_path(*parts: str) -> str:
    return str(_get_app().resource_path(*parts))


def storage_path(*parts: str) -> str:
    return str(_get_app().storage_path(*parts))


def view(template: str, data: dict | None = None) -> Any:
    factory = app("view")
    return factory.make(template, data or {})


def redirect(url: str = "/", status: int = 302) -> Any:
    from hunt.http.response import FluentRedirect

    return FluentRedirect(url, status)


def route(name: str, params: dict | None = None) -> str:
    router = app("router")
    return router.url(name, params or {})


def abort(status: int, message: str = "") -> None:
    from hunt.http.response import HttpException

    raise HttpException(status, message)


def session(key: str | None = None, default: Any = None) -> Any:
    from hunt.auth.manager import _get_current_request

    req = _get_current_request()
    if req is None:
        raise RuntimeError("No active request.")
    store = getattr(req, "_session", None)
    if store is None:
        raise RuntimeError("Session middleware is not active.")
    if key is None:
        return store
    return store.get(key, default)


def cache(key: str | None = None, default: Any = None) -> Any:
    from hunt.cache.manager import Cache

    if key is None:
        return Cache
    return Cache.get(key, default)


def logger(channel: str = "default") -> Any:
    from hunt.log.manager import Log

    return Log


def event(name: str | Any, payload: Any = None) -> Any:
    from hunt.events.dispatcher import Dispatcher

    return Dispatcher.dispatch_sync(name, payload)


def auth() -> Any:
    from hunt.auth.manager import Auth

    return Auth


def hash_password(password: str) -> str:
    from hunt.auth.manager import hash_password as _hash

    return _hash(password)


def __(key: str, replace: dict | None = None, locale: str | None = None) -> str:
    try:
        return app("translator").get(key, replace or {}, locale)
    except Exception:
        return key


def trans(key: str, replace: dict | None = None, locale: str | None = None) -> str:
    return __(key, replace, locale)


def trans_choice(key: str, count: int, replace: dict | None = None, locale: str | None = None) -> str:
    try:
        return app("translator").choice(key, count, replace or {}, locale)
    except Exception:
        return key


def back(default: str = "/") -> Any:
    """Redirect to the previous URL (stored in session) or a fallback.

    Delegates to ``redirect().back()`` so both spellings share one implementation,
    including its open-redirect protection.
    """
    from hunt.http.response import FluentRedirect

    return FluentRedirect(default).back(default)
