from __future__ import annotations

import inspect
from typing import Any, Callable, Type, TypeVar

T = TypeVar("T")


class BindingResolutionError(Exception):
    pass


class Container:
    def __init__(self) -> None:
        self._bindings: dict[str, dict] = {}
        self._instances: dict[str, Any] = {}
        self._aliases: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def bind(self, abstract: str | Type, concrete: Callable | None = None, shared: bool = False) -> None:
        key = self._key(abstract)
        if concrete is None:
            concrete = abstract
        self._bindings[key] = {"concrete": concrete, "shared": shared}

    def singleton(self, abstract: str | Type, concrete: Callable | None = None) -> None:
        self.bind(abstract, concrete, shared=True)

    def instance(self, abstract: str | Type, instance: Any) -> None:
        key = self._key(abstract)
        self._instances[key] = instance

    def alias(self, abstract: str | Type, alias: str) -> None:
        self._aliases[alias] = self._key(abstract)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def make(self, abstract: str | Type, params: dict | None = None) -> Any:
        key = self._key(abstract)
        key = self._aliases.get(key, key)

        if key in self._instances:
            return self._instances[key]

        binding = self._bindings.get(key)
        if binding is None:
            # Try to auto-resolve if it's a class
            if inspect.isclass(abstract):
                return self._build(abstract, params or {})
            raise BindingResolutionError(f"No binding found for '{key}'")

        concrete = binding["concrete"]
        obj = self._build(concrete, params or {})

        if binding["shared"]:
            self._instances[key] = obj

        return obj

    def call(self, callback: Callable, params: dict | None = None) -> Any:
        resolved = self._resolve_dependencies(callback, params or {})
        return callback(**resolved)

    def bound(self, abstract: str | Type) -> bool:
        key = self._key(abstract)
        return key in self._bindings or key in self._instances

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build(self, concrete: Callable, params: dict) -> Any:
        if not callable(concrete):
            raise BindingResolutionError(f"'{concrete}' is not callable")

        if inspect.isclass(concrete):
            resolved = self._resolve_dependencies(concrete.__init__, params, skip_self=True)
            return concrete(**resolved)

        resolved = self._resolve_dependencies(concrete, params)
        return concrete(**resolved)

    def _resolve_dependencies(self, func: Callable, overrides: dict, skip_self: bool = False) -> dict:
        try:
            sig = inspect.signature(func)
        except (ValueError, TypeError):
            return overrides

        resolved: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if skip_self and name == "self":
                continue
            if name in overrides:
                resolved[name] = overrides[name]
                continue
            if param.annotation is inspect.Parameter.empty:
                if param.default is not inspect.Parameter.empty:
                    resolved[name] = param.default
                continue
            annotation = param.annotation
            if inspect.isclass(annotation) and self.bound(annotation):
                resolved[name] = self.make(annotation)
            elif param.default is not inspect.Parameter.empty:
                resolved[name] = param.default

        return resolved

    @staticmethod
    def _key(abstract: str | Type) -> str:
        if isinstance(abstract, str):
            return abstract
        return f"{abstract.__module__}.{abstract.__qualname__}"
