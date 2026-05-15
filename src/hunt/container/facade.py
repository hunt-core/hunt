from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.application import Application

_app: Application | None = None


def set_facade_application(app: Application) -> None:
    global _app
    _app = app


class Facade:
    """Base facade that proxies static calls to a container binding."""

    @classmethod
    def _facade_accessor(cls) -> str:
        raise NotImplementedError(f"{cls.__name__} must define _facade_accessor()")

    @classmethod
    def _facade_root(cls) -> Any:
        if _app is None:
            raise RuntimeError("Application not bootstrapped yet")
        return _app.make(cls._facade_accessor())

    def __class_getitem__(cls, item: Any) -> Any:
        return cls

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    def __class__(self) -> Any:  # type: ignore[override]
        return self._facade_root()

    @classmethod
    def __getattr__(cls, name: str) -> Any:  # type: ignore[misc]
        return getattr(cls._facade_root(), name)
