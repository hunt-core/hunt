from __future__ import annotations

from typing import Any


class Seeder:
    """Base class for database seeders."""

    def run(self) -> None:
        raise NotImplementedError

    def call(self, *seeder_classes: type) -> None:
        for cls in seeder_classes:
            instance = cls()
            instance.run()
