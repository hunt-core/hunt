from __future__ import annotations

import random
import string
from typing import Any, Callable, Type


class Factory:
    """Base class for model factories."""

    model: type | None = None

    def definition(self) -> dict[str, Any]:
        raise NotImplementedError

    def make(self, overrides: dict[str, Any] | None = None) -> Any:
        data = {**self.definition(), **(overrides or {})}
        instance = self.model.__new__(self.model)
        instance._attributes = data
        instance._exists = False
        return instance

    def create(self, overrides: dict[str, Any] | None = None) -> Any:
        data = {**self.definition(), **(overrides or {})}
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
