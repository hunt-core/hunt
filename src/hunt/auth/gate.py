from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any


def _is_async_callable(callback: Any) -> bool:
    """True for ``async def`` functions, partials of them and objects with an async ``__call__``."""
    target = callback
    while isinstance(target, (functools.partial, functools.partialmethod)):
        target = target.func
    if inspect.iscoroutinefunction(target) or inspect.isasyncgenfunction(target):
        return True
    if inspect.isfunction(target) or inspect.ismethod(target) or inspect.isclass(target):
        return False
    # Callable object: what matters is its __call__ method.
    return callable(target) and inspect.iscoroutinefunction(type(target).__call__)


def _require_sync(callback: Any, what: str) -> None:
    if not callable(callback):
        raise TypeError(f"Gate {what} must be callable, got {type(callback).__name__}.")
    if _is_async_callable(callback):
        raise TypeError(
            f"Gate {what} must be a synchronous callable: an async function returns a coroutine, "
            "which cannot be evaluated as an authorization decision."
        )


def _decision(result: Any) -> bool:
    """Turn a callback result into a decision, failing closed on awaitables.

    An unawaited coroutine is truthy, so ``bool(result)`` would authorize everyone.
    """
    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        return False
    return bool(result)


def _accepts(method: Any, count: int) -> bool:
    """True when *method* can be called with *count* positional arguments."""
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return True
    positional = 0
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional += 1
    return positional >= count


class _GateContext:
    """Gate bound to a specific user — used by Gate.for_user()."""

    def __init__(self, gate: _Gate, user: Any) -> None:
        self._gate = gate
        self._user = user

    def allows(self, ability: str, *args: Any) -> bool:
        return self._gate._check(ability, self._user, *args)

    def denies(self, ability: str, *args: Any) -> bool:
        return not self.allows(ability, *args)

    def any(self, abilities: list[str], *args: Any) -> bool:
        return any(self.allows(a, *args) for a in abilities)

    def none(self, abilities: list[str], *args: Any) -> bool:
        return not self.any(abilities, *args)

    def authorize(self, ability: str, *args: Any) -> None:
        if not self.allows(ability, *args):
            from hunt.http.response import HttpException

            raise HttpException(403, "This action is unauthorized.")


class _Gate:
    """Authorization gate — define abilities and policies, then check them."""

    def __init__(self) -> None:
        self._abilities: dict[str, Callable] = {}
        self._policies: dict[type, type] = {}
        self._before_callbacks: list[Callable] = []
        self._after_callbacks: list[Callable] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def define(self, ability: str, callback: Callable) -> _Gate:
        _require_sync(callback, f"ability {ability!r}")
        self._abilities[ability] = callback
        return self

    def policy(self, model: type, policy_class: type) -> _Gate:
        if not isinstance(policy_class, type):
            raise TypeError("Gate policy must be a class.")
        for name in dir(policy_class):
            if name.startswith("_"):
                continue
            attr = inspect.getattr_static(policy_class, name, None)
            if isinstance(attr, (staticmethod, classmethod)):
                attr = attr.__func__
            if callable(attr) and _is_async_callable(attr):
                raise TypeError(
                    f"Policy method {policy_class.__name__}.{name} must be synchronous: an async method "
                    "returns a coroutine, which cannot be evaluated as an authorization decision."
                )
        self._policies[model] = policy_class
        return self

    def before(self, callback: Callable) -> _Gate:
        """Run callback before every gate check. Return True/False to short-circuit."""
        _require_sync(callback, "before callback")
        self._before_callbacks.append(callback)
        return self

    def after(self, callback: Callable) -> _Gate:
        """Run callback after every gate check."""
        _require_sync(callback, "after callback")
        self._after_callbacks.append(callback)
        return self

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def allows(self, ability: str, *args: Any) -> bool:
        user = self._current_user()
        return self._check(ability, user, *args)

    def denies(self, ability: str, *args: Any) -> bool:
        return not self.allows(ability, *args)

    def any(self, abilities: list[str], *args: Any) -> bool:
        return any(self.allows(a, *args) for a in abilities)

    def none(self, abilities: list[str], *args: Any) -> bool:
        return not self.any(abilities, *args)

    def authorize(self, ability: str, *args: Any) -> None:
        """Raise HTTP 403 if the current user cannot perform the ability."""
        if not self.allows(ability, *args):
            from hunt.http.response import HttpException

            raise HttpException(403, "This action is unauthorized.")

    def for_user(self, user: Any) -> _GateContext:
        """Return a gate context scoped to a specific user."""
        return _GateContext(self, user)

    def can(self, ability: str, *args: Any) -> bool:
        """Alias for allows() — matches Laravel's $user->can() API."""
        return self.allows(ability, *args)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _current_user(self) -> Any:
        try:
            from hunt.auth.manager import Auth

            return Auth.user()
        except Exception:
            return None

    def _policy_for(self, target: Any) -> type | None:
        """Find the policy for a model instance or class, honouring inheritance."""
        model_class = target if isinstance(target, type) else type(target)
        for klass in getattr(model_class, "__mro__", (model_class,)):
            policy_class = self._policies.get(klass)
            if policy_class is not None:
                return policy_class
        return None

    def _check(self, ability: str, user: Any, *args: Any) -> bool:
        # Before callbacks
        for cb in self._before_callbacks:
            result = cb(user, ability)
            if result is not None:
                return _decision(result)

        # Deny guests by default unless the ability explicitly allows it
        if user is None:
            return False

        # Try policy
        if args:
            policy_class = self._policy_for(args[0])
            if policy_class is not None:
                result = self._check_policy(policy_class, ability, user, *args)
                self._run_after(user, ability, result)
                return result

        # Try defined ability callback
        callback = self._abilities.get(ability)
        if callback is not None:
            result = _decision(callback(user, *args))
            self._run_after(user, ability, result)
            return result

        self._run_after(user, ability, False)
        return False

    def _check_policy(self, policy_class: type, ability: str, user: Any, *args: Any) -> bool:
        policy = policy_class()
        before = getattr(policy, "before", None)
        if callable(before):
            pre = before(user, ability)
            if pre is not None:
                return _decision(pre)
        # Only public policy methods are abilities: never dunders/private helpers, and
        # never the ``before`` hook itself (its signature and meaning are different).
        if not isinstance(ability, str) or not ability.isidentifier() or ability.startswith("_") or ability == "before":
            return False
        method = getattr(policy, ability, None)
        if method is None or not callable(method) or isinstance(method, type):
            return False
        # `Gate.allows("create", Post)` passes the model class only to find the
        # policy; class-level abilities are declared as `create(self, user)`.
        call_args = args
        if args and isinstance(args[0], type) and not _accepts(method, 1 + len(args)):
            call_args = args[1:]
        return _decision(method(user, *call_args))

    def _run_after(self, user: Any, ability: str, result: bool) -> None:
        for cb in self._after_callbacks:
            outcome = cb(user, ability, result)
            if inspect.isawaitable(outcome):
                close = getattr(outcome, "close", None)
                if callable(close):
                    close()


Gate = _Gate()
