from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from typing import Any, TypeVar

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

    def bind(self, abstract: str | type, concrete: Callable | None = None, shared: bool = False) -> None:
        key = self._key(abstract)
        if concrete is None:
            concrete = abstract
        self._bindings[key] = {"concrete": concrete, "shared": shared}

    def singleton(self, abstract: str | type, concrete: Callable | None = None) -> None:
        self.bind(abstract, concrete, shared=True)

    def instance(self, abstract: str | type, instance: Any) -> None:
        key = self._key(abstract)
        self._instances[key] = instance

    def alias(self, abstract: str | type, alias: str) -> None:
        self._aliases[alias] = self._key(abstract)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def make(self, abstract: str | type, params: dict | None = None) -> Any:
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

    def bound(self, abstract: str | type) -> bool:
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

    @staticmethod
    def _signature(func: Callable) -> inspect.Signature:
        """Signature with string annotations (``from __future__ import annotations``) resolved.

        Annotations are resolved one by one when resolving them all at once fails, so a
        single name that only exists under ``TYPE_CHECKING`` does not hide the others.
        Annotations that cannot be resolved stay strings.
        """
        sig = inspect.signature(func)
        if not any(isinstance(p.annotation, str) for p in sig.parameters.values()):
            return sig
        target = getattr(func, "__func__", func)
        try:
            target = inspect.unwrap(target)
        except ValueError:
            pass
        globalns = getattr(target, "__globals__", None)
        if globalns is None:
            module = inspect.getmodule(target)
            globalns = getattr(module, "__dict__", {})

        def _resolve(name: str, annotation: str) -> Any:
            def _probe() -> None:  # carrier for a single annotation
                return None

            _probe.__annotations__ = {name: annotation}
            try:
                return typing.get_type_hints(_probe, globalns=dict(globalns))[name]
            except Exception:
                return annotation

        params = [
            p.replace(annotation=_resolve(p.name, p.annotation)) if isinstance(p.annotation, str) else p
            for p in sig.parameters.values()
        ]
        return sig.replace(parameters=params)

    def _resolve_dependencies(self, func: Callable, overrides: dict, skip_self: bool = False) -> dict:
        try:
            sig = self._signature(func)
        except (ValueError, TypeError):
            return overrides

        resolved: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if skip_self and name == "self":
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if name in overrides:
                resolved[name] = overrides[name]
                continue
            has_default = param.default is not inspect.Parameter.empty
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                if has_default:
                    resolved[name] = param.default
                continue
            if inspect.isclass(annotation) and self.bound(annotation):
                resolved[name] = self.make(annotation)
            elif has_default:
                resolved[name] = param.default
            elif isinstance(annotation, str):
                owner = getattr(func, "__qualname__", repr(func))
                raise BindingResolutionError(
                    f"Cannot resolve parameter '{name}' of {owner}: annotation {annotation!r} could not be "
                    "evaluated. Import the type at module level (not only under TYPE_CHECKING) or pass the "
                    "value explicitly."
                )

        return resolved

    @staticmethod
    def _key(abstract: str | type) -> str:
        if isinstance(abstract, str):
            return abstract
        return f"{abstract.__module__}.{abstract.__qualname__}"
