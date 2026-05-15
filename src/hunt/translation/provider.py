from __future__ import annotations

from hunt.container.provider import ServiceProvider


class TranslationServiceProvider(ServiceProvider):
    def register(self) -> None:
        app = self.app

        def _make() -> object:
            from hunt.translation.translator import Translator

            lang_path = app.path("lang")
            locale = app.config.get("app.locale", "en")
            fallback = app.config.get("app.fallback_locale", "en")
            return Translator(lang_path, locale=locale, fallback_locale=fallback)

        app.singleton("translator", _make)

    def boot(self) -> None:
        pass
