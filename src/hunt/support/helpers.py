from __future__ import annotations

import os
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


def env(key: str, default: Any = None) -> Any:
    val = os.environ.get(key)
    if val is None:
        return default
    low = val.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def dump(*values: Any) -> Any:
    import pprint

    for v in values:
        pprint.pprint(v)
    return values[-1] if values else None


def dd(*values: Any) -> None:
    import pprint

    for v in values:
        pprint.pprint(v)
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
    """Redirect to the previous URL (stored in session) or a fallback."""
    from hunt.auth.manager import _get_current_request
    from hunt.http.response import RedirectResponse

    req = _get_current_request()
    store = getattr(req, "_session", None) if req else None
    url = store.get("_previous_url", default) if store else default
    return RedirectResponse(url)
