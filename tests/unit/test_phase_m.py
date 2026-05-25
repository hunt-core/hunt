"""Phase M — Localization / Translation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hunt.translation.translator import Translator

# ---------------------------------------------------------------------------
# Fixtures — minimal lang directory wired up in a tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture()
def lang(tmp_path: Path) -> Path:
    en = tmp_path / "en"
    en.mkdir()
    (en / "messages.py").write_text(
        "messages = {\n"
        '    "welcome": "Welcome!",\n'
        '    "greeting": "Hello, :name!",\n'
        '    "farewell": "Goodbye, :Name!",\n'
        '    "shout": "HELLO, :NAME!",\n'
        '    "nested": {"deep": "Found it"},\n'
        "}\n"
    )
    (en / "auth.py").write_text(
        "auth = {\n"
        '    "failed": "These credentials do not match our records.",\n'
        '    "throttle": "Too many attempts. Try again in :seconds seconds.",\n'
        "}\n"
    )
    (en / "plural.py").write_text(
        "plural = {\n"
        '    "simple": "one apple|many apples",\n'
        '    "qualified": "{0} no apples|{1} one apple|[2,*] :count apples",\n'
        '    "range": "[1,1] one item|[2,10] a few items|[11,*] lots of items",\n'
        '    "zero_exact": "{0} nothing here|{1} one thing|[2,*] :count things",\n'
        "}\n"
    )
    es = tmp_path / "es"
    es.mkdir()
    (es / "messages.py").write_text('messages = {\n    "welcome": "Bienvenido!",\n}\n')
    return tmp_path


@pytest.fixture()
def t(lang: Path) -> Translator:
    return Translator(lang, locale="en", fallback_locale="en")


# ---------------------------------------------------------------------------
# Basic key resolution
# ---------------------------------------------------------------------------


class TestBasicResolution:
    def test_resolves_simple_key(self, t):
        assert t.get("messages.welcome") == "Welcome!"

    def test_resolves_key_in_auth_group(self, t):
        assert t.get("auth.failed") == "These credentials do not match our records."

    def test_missing_key_returns_key(self, t):
        assert t.get("messages.does_not_exist") == "messages.does_not_exist"

    def test_missing_group_returns_key(self, t):
        assert t.get("nosuchgroup.key") == "nosuchgroup.key"

    def test_key_without_group_returns_key(self, t):
        assert t.get("welcome") == "welcome"

    def test_nested_key(self, t):
        assert t.get("messages.nested.deep") == "Found it"

    def test_nested_key_missing_intermediate(self, t):
        assert t.get("messages.nested.missing") == "messages.nested.missing"


# ---------------------------------------------------------------------------
# Replacements
# ---------------------------------------------------------------------------


class TestReplacements:
    def test_basic_replacement(self, t):
        assert t.get("messages.greeting", {"name": "Alice"}) == "Hello, Alice!"

    def test_no_replacement_needed(self, t):
        assert t.get("messages.welcome") == "Welcome!"

    def test_multiple_replacements(self, t):
        assert t.get("auth.throttle", {"seconds": "30"}) == "Too many attempts. Try again in 30 seconds."

    def test_capitalised_placeholder(self, t):
        # :Name → capitalised value
        assert t.get("messages.farewell", {"name": "alice"}) == "Goodbye, Alice!"

    def test_upper_placeholder(self, t):
        # :NAME → uppercased value
        assert t.get("messages.shout", {"name": "alice"}) == "HELLO, ALICE!"


# ---------------------------------------------------------------------------
# Locale switching
# ---------------------------------------------------------------------------


class TestLocaleSwitching:
    def test_get_locale_default(self, lang):
        t = Translator(lang, locale="en")
        assert t.get_locale() == "en"

    def test_set_locale(self, lang):
        t = Translator(lang, locale="en")
        t.set_locale("es")
        assert t.get_locale() == "es"

    def test_resolves_in_switched_locale(self, lang):
        t = Translator(lang, locale="es")
        assert t.get("messages.welcome") == "Bienvenido!"

    def test_locale_param_overrides_current(self, lang):
        t = Translator(lang, locale="en")
        assert t.get("messages.welcome", locale="es") == "Bienvenido!"

    def test_current_locale_unchanged_after_param_override(self, lang):
        t = Translator(lang, locale="en")
        t.get("messages.welcome", locale="es")
        assert t.get_locale() == "en"


# ---------------------------------------------------------------------------
# Fallback locale
# ---------------------------------------------------------------------------


class TestFallback:
    def test_falls_back_when_key_missing_in_locale(self, lang):
        # "es" locale has messages.welcome but not messages.greeting
        t = Translator(lang, locale="es", fallback_locale="en")
        assert t.get("messages.greeting", {"name": "World"}) == "Hello, World!"

    def test_no_fallback_when_key_exists(self, lang):
        t = Translator(lang, locale="es", fallback_locale="en")
        assert t.get("messages.welcome") == "Bienvenido!"

    def test_fallback_locale_configurable(self, lang):
        t = Translator(lang, locale="fr", fallback_locale="en")
        assert t.get("messages.welcome") == "Welcome!"

    def test_set_fallback(self, lang):
        t = Translator(lang, locale="fr")
        t.set_fallback("en")
        assert t.get("messages.welcome") == "Welcome!"


# ---------------------------------------------------------------------------
# has()
# ---------------------------------------------------------------------------


class TestHas:
    def test_has_existing_key(self, t):
        assert t.has("messages.welcome") is True

    def test_has_missing_key(self, t):
        assert t.has("messages.nope") is False

    def test_has_missing_group(self, t):
        assert t.has("missing.key") is False

    def test_has_with_locale_param(self, lang):
        t = Translator(lang, locale="en")
        assert t.has("messages.welcome", locale="es") is True
        assert t.has("messages.greeting", locale="es") is False


# ---------------------------------------------------------------------------
# Pluralization — simple ngettext-style
# ---------------------------------------------------------------------------


class TestSimplePlural:
    def test_singular(self, t):
        assert t.choice("plural.simple", 1) == "one apple"

    def test_plural(self, t):
        assert t.choice("plural.simple", 2) == "many apples"

    def test_zero_uses_plural(self, t):
        assert t.choice("plural.simple", 0) == "many apples"

    def test_large_count_uses_plural(self, t):
        assert t.choice("plural.simple", 100) == "many apples"


# ---------------------------------------------------------------------------
# Pluralization — Laravel qualified-style
# ---------------------------------------------------------------------------


class TestQualifiedPlural:
    def test_exact_zero(self, t):
        assert t.choice("plural.qualified", 0) == "no apples"

    def test_exact_one(self, t):
        assert t.choice("plural.qualified", 1) == "one apple"

    def test_range_open(self, t):
        assert t.choice("plural.qualified", 2) == "2 apples"

    def test_count_replacement(self, t):
        assert t.choice("plural.qualified", 5) == "5 apples"

    def test_explicit_count_replacement(self, t):
        assert t.choice("plural.qualified", 7, {"count": 7}) == "7 apples"

    def test_range_boundaries(self, t):
        assert t.choice("plural.range", 1) == "one item"
        assert t.choice("plural.range", 2) == "a few items"
        assert t.choice("plural.range", 10) == "a few items"
        assert t.choice("plural.range", 11) == "lots of items"

    def test_zero_exact_qualifier(self, t):
        assert t.choice("plural.zero_exact", 0) == "nothing here"
        assert t.choice("plural.zero_exact", 1) == "one thing"
        assert t.choice("plural.zero_exact", 3) == "3 things"


# ---------------------------------------------------------------------------
# choice() with replacements + missing key
# ---------------------------------------------------------------------------


class TestChoiceEdgeCases:
    def test_missing_key_returns_key(self, t):
        assert t.choice("plural.nope", 1) == "plural.nope"

    def test_replacement_in_plural(self, t):
        result = t.choice("plural.qualified", 9, {"extra": "x"})
        assert "9" in result

    def test_choice_locale_param(self, lang):
        t = Translator(lang, locale="en")
        # es has no plural group; falls back to en
        result = t.choice("plural.simple", 1, locale="es")
        assert result == "one apple"


# ---------------------------------------------------------------------------
# Lang file loading
# ---------------------------------------------------------------------------


class TestLangFileLoading:
    def test_groups_cached_after_first_load(self, t):
        t.get("messages.welcome")
        t.get("messages.greeting", {"name": "X"})
        # Both hits; cache key is (locale, group)
        assert ("en", "messages") in t._cache

    def test_missing_group_cached_as_empty(self, t):
        t.get("nosuchgroup.key")
        assert t._cache.get(("en", "nosuchgroup")) == {}

    def test_lang_file_with_no_matching_attr_uses_first_dict(self, tmp_path):
        en = tmp_path / "en"
        en.mkdir()
        # File uses a different variable name
        (en / "custom.py").write_text("MY_DICT = {'hello': 'world'}\n")
        tr = Translator(tmp_path)
        assert tr.get("custom.hello") == "world"

    def test_locale_directory_missing(self, tmp_path):
        tr = Translator(tmp_path)  # tmp_path has no "en" subdir
        assert tr.get("messages.welcome") == "messages.welcome"


# ---------------------------------------------------------------------------
# Application.locale() / set_locale()
# ---------------------------------------------------------------------------


class TestApplicationLocale:
    def test_set_and_get_locale(self):
        from hunt.application import Application

        mock_translator = MagicMock()
        mock_translator.get_locale.return_value = "fr"

        with patch.object(Application, "make", return_value=mock_translator):
            app = object.__new__(Application)
            app.set_locale("fr")
            assert app.locale() == "fr"

    def test_locale_fallback_when_translator_missing(self):
        from hunt.application import Application
        from hunt.config.repository import ConfigRepository

        mock_config = MagicMock(spec=ConfigRepository)
        mock_config.get.return_value = "de"

        app = MagicMock(spec=Application)
        app.make.side_effect = [RuntimeError("not bound"), mock_config]
        app.config = mock_config

        # Call the real locale() with the mock app as self
        result = Application.locale(app)
        assert result == "de"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_double_underscore_helper(self, lang):
        tr = Translator(lang)
        mock_app = MagicMock()
        mock_app.return_value = tr

        import hunt.support.helpers as helpers

        with patch.object(helpers, "app", mock_app):
            result = helpers.__("messages.welcome")
        assert result == "Welcome!"

    def test_trans_alias(self, lang):
        tr = Translator(lang)
        mock_app = MagicMock(return_value=tr)

        import hunt.support.helpers as helpers

        with patch.object(helpers, "app", mock_app):
            assert helpers.trans("messages.welcome") == "Welcome!"

    def test_trans_choice_helper(self, lang):
        tr = Translator(lang)
        mock_app = MagicMock(return_value=tr)

        import hunt.support.helpers as helpers

        with patch.object(helpers, "app", mock_app):
            assert helpers.trans_choice("plural.simple", 1) == "one apple"
            assert helpers.trans_choice("plural.simple", 5) == "many apples"

    def test_double_underscore_returns_key_on_error(self):
        import hunt.support.helpers as helpers

        with patch.object(helpers, "app", side_effect=RuntimeError("no app")):
            assert helpers.__("some.key") == "some.key"

    def test_trans_choice_returns_key_on_error(self):
        import hunt.support.helpers as helpers

        with patch.object(helpers, "app", side_effect=RuntimeError("no app")):
            assert helpers.trans_choice("some.key", 3) == "some.key"


# ---------------------------------------------------------------------------
# TranslationServiceProvider
# ---------------------------------------------------------------------------


class TestTranslationServiceProvider:
    def test_registers_translator_singleton(self, lang):
        from hunt.config.repository import ConfigRepository
        from hunt.translation.provider import TranslationServiceProvider

        mock_app = MagicMock()
        mock_app.path.return_value = lang
        mock_config = MagicMock(spec=ConfigRepository)
        mock_config.get.side_effect = lambda key, default=None: {
            "app.locale": "en",
            "app.fallback_locale": "en",
        }.get(key, default)
        mock_app.config = mock_config

        provider = TranslationServiceProvider(mock_app)
        provider.register()

        # singleton() was called with "translator" and a factory
        mock_app.singleton.assert_called_once()
        call_args = mock_app.singleton.call_args
        assert call_args[0][0] == "translator"

    def test_boot_is_noop(self, lang):
        from hunt.translation.provider import TranslationServiceProvider

        provider = TranslationServiceProvider(MagicMock())
        provider.boot()  # should not raise
