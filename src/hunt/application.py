from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from hunt.config.loader import load_config_directory
from hunt.config.repository import ConfigRepository
from hunt.container.container import Container
from hunt.container.facade import set_facade_application
from hunt.container.provider import ServiceProvider


class Application(Container):
    """Central application class — bootstraps providers, config, and env."""

    VERSION = "0.1.0"

    def __init__(self, base_path: Path | str | None = None) -> None:
        super().__init__()
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self._providers: list[ServiceProvider] = []
        self._booted = False
        self._error_handlers: list[Callable] = []
        self._startup_handlers: list[Callable] = []
        self._shutdown_handlers: list[Callable] = []

        self._load_env()
        self._bind_paths()
        self._load_config()
        self._setup_observability()

        self.instance("app", self)
        set_facade_application(self)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def bootstrap(self, providers: list[type[ServiceProvider]] | None = None) -> None:
        for cls in providers or []:
            provider = cls(self)
            provider.register()
            self._providers.append(provider)

        for provider in self._providers:
            provider.boot()

        self._booted = True

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def path(self, *parts: str) -> Path:
        return self.base_path.joinpath(*parts)

    def app_path(self, *parts: str) -> Path:
        return self.path("app", *parts)

    def config_path(self, *parts: str) -> Path:
        return self.path("config", *parts)

    def database_path(self, *parts: str) -> Path:
        return self.path("database", *parts)

    def resource_path(self, *parts: str) -> Path:
        return self.path("resources", *parts)

    def storage_path(self, *parts: str) -> Path:
        return self.path("storage", *parts)

    def route_path(self, *parts: str) -> Path:
        return self.path("routes", *parts)

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    def env(self, key: str, default: Any = None) -> Any:
        return os.environ.get(key, default)

    def is_production(self) -> bool:
        return self.env("APP_ENV", "production") == "production"

    def is_local(self) -> bool:
        return self.env("APP_ENV", "production") == "local"

    def locale(self) -> str:
        try:
            return self.make("translator").get_locale()
        except Exception:
            return self.config.get("app.locale", "en")

    def set_locale(self, locale: str) -> None:
        try:
            self.make("translator").set_locale(locale)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_env(self) -> None:
        env_file = self.base_path / ".env"
        if env_file.exists():
            load_dotenv(env_file, override=False)

    def _bind_paths(self) -> None:
        self.instance("path", str(self.base_path))
        self.instance("path.app", str(self.app_path()))
        self.instance("path.config", str(self.config_path()))
        self.instance("path.database", str(self.database_path()))
        self.instance("path.resources", str(self.resource_path()))
        self.instance("path.storage", str(self.storage_path()))

    def on_startup(self, handler: Callable) -> None:
        """Register a callback to run when the ASGI server starts up.

        The callback may be async or sync. It receives no arguments.
        """
        self._startup_handlers.append(handler)

    def on_shutdown(self, handler: Callable) -> None:
        """Register a callback to run when the ASGI server shuts down.

        The callback may be async or sync. It receives no arguments.
        """
        self._shutdown_handlers.append(handler)

    def on_error(self, handler: Callable) -> None:
        """Register a hook called whenever an unhandled exception reaches the kernel.

        The hook receives ``(exc: Exception, request: Request)`` and should not
        raise — any exception thrown by the hook is silently swallowed.
        """
        self._error_handlers.append(handler)

    def _load_config(self) -> None:
        raw = load_config_directory(self.config_path())
        config = ConfigRepository(raw)
        self.instance("config", config)

    def _setup_observability(self) -> None:
        dsn = os.environ.get("SENTRY_DSN", "")
        if not dsn:
            return
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=dsn)

            def _sentry_hook(exc: Exception, request: Any = None) -> None:
                sentry_sdk.capture_exception(exc)

            self._error_handlers.append(_sentry_hook)
        except ImportError:
            import warnings

            warnings.warn(
                "SENTRY_DSN is set but sentry-sdk is not installed. Install it with: pip install sentry-sdk",
                RuntimeWarning,
                stacklevel=2,
            )

    @property
    def config(self) -> ConfigRepository:
        return self.make("config")
