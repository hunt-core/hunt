from __future__ import annotations

from collections.abc import Callable
from typing import Any


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
        self._abilities[ability] = callback
        return self

    def policy(self, model: type, policy_class: type) -> _Gate:
        self._policies[model] = policy_class
        return self

    def before(self, callback: Callable) -> _Gate:
        """Run callback before every gate check. Return True/False to short-circuit."""
        self._before_callbacks.append(callback)
        return self

    def after(self, callback: Callable) -> _Gate:
        """Run callback after every gate check."""
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

    def _check(self, ability: str, user: Any, *args: Any) -> bool:
        # Before callbacks
        for cb in self._before_callbacks:
            result = cb(user, ability)
            if result is not None:
                return bool(result)

        # Deny guests by default unless the ability explicitly allows it
        if user is None:
            return False

        # Try policy
        if args:
            model_instance = args[0]
            model_class = type(model_instance) if not isinstance(model_instance, type) else model_instance
            policy_class = self._policies.get(model_class)
            if policy_class is not None:
                result = self._check_policy(policy_class, ability, user, *args)
                self._run_after(user, ability, result)
                return result

        # Try defined ability callback
        callback = self._abilities.get(ability)
        if callback is not None:
            result = bool(callback(user, *args))
            self._run_after(user, ability, result)
            return result

        self._run_after(user, ability, False)
        return False

    def _check_policy(self, policy_class: type, ability: str, user: Any, *args: Any) -> bool:
        policy = policy_class()
        if hasattr(policy, "before"):
            pre = policy.before(user, ability)
            if pre is not None:
                return bool(pre)
        method = getattr(policy, ability, None)
        if method is None:
            return False
        return bool(method(user, *args))

    def _run_after(self, user: Any, ability: str, result: bool) -> None:
        for cb in self._after_callbacks:
            cb(user, ability, result)


Gate = _Gate()
