from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.application import Application

_app: Application | None = None


def set_facade_application(app: Application) -> None:
    global _app
    _app = app


class _FacadeMeta(type):
    """Forward unknown *class* attributes to the facade's container binding.

    ``__getattr__`` defined on a class only handles instance attribute access, so the
    proxy has to live on the metaclass for ``Cache.get(...)`` style calls to work.
    """

    def __getattr__(cls, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return getattr(cls._facade_root(), name)


class Facade(metaclass=_FacadeMeta):
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

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return getattr(type(self)._facade_root(), name)
