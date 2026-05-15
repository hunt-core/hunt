from __future__ import annotations

import random
import string
from collections.abc import Callable
from typing import Any, ClassVar


class Factory:
    """Base class for model factories.

    Subclasses define ``definition()`` for base attribute values and optionally
    ``states`` to declare named overrides::

        class UserFactory(Factory):
            model = User
            states = {
                "admin": lambda: {"role": "admin"},
                "banned": lambda: {"banned_at": "2025-01-01"},
            }

            def definition(self):
                return {"name": "Alice", "email": self.random_email(), "role": "user"}

        user = UserFactory().state("admin").make()
    """

    model: type | None = None
    states: ClassVar[dict[str, Callable[[], dict[str, Any]]]] = {}

    def __init__(self) -> None:
        self._state_overrides: dict[str, Any] = {}

    def definition(self) -> dict[str, Any]:
        raise NotImplementedError

    def state(self, name: str) -> Factory:
        """Apply named state overrides. Returns self for chaining."""
        state_fn = self.states.get(name)
        if state_fn is None:
            raise ValueError(f"Unknown factory state: {name!r}")
        self._state_overrides.update(state_fn())
        return self

    def make(self, overrides: dict[str, Any] | None = None) -> Any:
        data = {**self.definition(), **self._state_overrides, **(overrides or {})}
        instance = self.model.__new__(self.model)
        instance._attributes = data
        instance._exists = False
        return instance

    def create(self, overrides: dict[str, Any] | None = None) -> Any:
        data = {**self.definition(), **self._state_overrides, **(overrides or {})}
        return self.model.create(**data)

    def make_many(self, count: int, overrides: dict[str, Any] | None = None) -> list:
        return [self.make(overrides) for _ in range(count)]

    def create_many(self, count: int, overrides: dict[str, Any] | None = None) -> list:
        return [self.create(overrides) for _ in range(count)]

    # ------------------------------------------------------------------
    # Faker helpers (stdlib-only, no faker package required)
    # ------------------------------------------------------------------

    @staticmethod
    def random_string(length: int = 16) -> str:
        return "".join(random.choices(string.ascii_lowercase, k=length))

    @staticmethod
    def random_email() -> str:
        user = Factory.random_string(8)
        domain = Factory.random_string(6)
        return f"{user}@{domain}.com"

    @staticmethod
    def random_int(min_val: int = 1, max_val: int = 1000) -> int:
        return random.randint(min_val, max_val)

    @staticmethod
    def random_bool() -> bool:
        return random.choice([True, False])
