from __future__ import annotations


class Seeder:
    """Base class for database seeders."""

    def run(self) -> None:
        raise NotImplementedError

    def call(self, *seeder_classes: type) -> None:
        for cls in seeder_classes:
            instance = cls()
            instance.run()
