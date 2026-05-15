from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.application import Application


class ServiceProvider:
    def __init__(self, app: Application) -> None:
        self.app = app

    def register(self) -> None:
        """Bind things into the container."""

    def boot(self) -> None:
        """Bootstrap any application services."""
