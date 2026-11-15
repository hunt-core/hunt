"""Regression tests for AUTH-L8..L11 and the container half of HTTP-11."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from hunt.container import facade as facade_mod
from hunt.container.container import BindingResolutionError, Container
from hunt.container.facade import Facade
from hunt.support.collection import Collection
from hunt.support.helpers import env, env_bool, env_float, env_int
from hunt.support.str import Str
from hunt.translation.translator import Translator

if TYPE_CHECKING:
    from decimal import Decimal  # deliberately unavailable at runtime


# ---------------------------------------------------------------------------
# AUTH-L8: env()
# ---------------------------------------------------------------------------


class TestEnv:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0123", "0123"),  # leading zero: a password or zip code, not 123
            ("1e5", "1e5"),
            ("1.5", "1.5"),
            ("+5", "+5"),
            ("1_000", "1_000"),
            (" 42", " 42"),
            ("-0", "-0"),
            ("nan", "nan"),
            ("inf", "inf"),
            ("", ""),
            ("42", 42),
            ("-7", -7),
            ("0", 0),
            ("true", True),
            ("TRUE", True),
            ("(true)", True),
            ("false", False),
            ("False", False),
            ("null", None),
            ("(null)", None),
            ("(empty)", ""),
            ("hello", "hello"),
        ],
    )
    def test_coercion(self, monkeypatch, raw, expected):
        monkeypatch.setenv("HUNT_FIX_ENV", raw)
        value = env("HUNT_FIX_ENV", "default")
        assert value == expected
        assert type(value) is type(expected)

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("HUNT_FIX_ENV", raising=False)
        assert env("HUNT_FIX_ENV") is None
        assert env("HUNT_FIX_ENV", "fallback") == "fallback"

    def test_typed_readers(self, monkeypatch):
        monkeypatch.setenv("HUNT_FIX_ENV", "0123")
        assert env_int("HUNT_FIX_ENV") == 123
        monkeypatch.setenv("HUNT_FIX_ENV", "1e5")
        assert env_float("HUNT_FIX_ENV") == 100000.0
        assert env_int("HUNT_FIX_ENV", 7) == 7
        monkeypatch.setenv("HUNT_FIX_ENV", "garbage")
        assert env_int("HUNT_FIX_ENV", 3) == 3
        assert env_float("HUNT_FIX_ENV", 1.5) == 1.5
        assert env_bool("HUNT_FIX_ENV", True) is True
        for raw, expected in (("1", True), ("yes", True), ("ON", True), ("0", False), ("no", False), ("off", False)):
            monkeypatch.setenv("HUNT_FIX_ENV", raw)
            assert env_bool("HUNT_FIX_ENV") is expected
        monkeypatch.delenv("HUNT_FIX_ENV")
        assert env_int("HUNT_FIX_ENV") is None
        assert env_bool("HUNT_FIX_ENV") is False


# ---------------------------------------------------------------------------
# AUTH-L9: facades
# ---------------------------------------------------------------------------


class _Service:
    name = "svc"

    def greet(self, who: str) -> str:
        return f"hello {who}"


class _ServiceFacade(Facade):
    @classmethod
    def _facade_accessor(cls) -> str:
        return "service"


class TestFacade:
    def setup_method(self):
        self._previous = facade_mod._app
        app = MagicMock()
        app.make.side_effect = lambda key: _Service() if key == "service" else None
        facade_mod.set_facade_application(app)

    def teardown_method(self):
        facade_mod._app = self._previous

    def test_class_attribute_access_is_proxied(self):
        assert _ServiceFacade.greet("world") == "hello world"
        assert _ServiceFacade.name == "svc"

    def test_instance_access_is_proxied_and_class_is_intact(self):
        instance = _ServiceFacade()
        assert instance.greet("x") == "hello x"
        assert type(instance) is _ServiceFacade
        assert isinstance(instance, Facade)

    def test_missing_attribute_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            _ServiceFacade.nope  # noqa: B018
        assert not hasattr(_ServiceFacade, "__wrapped__")

    def test_unbootstrapped_facade_raises_clear_error(self):
        facade_mod._app = None
        with pytest.raises(RuntimeError, match="not bootstrapped"):
            _ServiceFacade.greet("x")

    def test_base_facade_requires_accessor(self):
        with pytest.raises(NotImplementedError):
            Facade.anything  # noqa: B018


# ---------------------------------------------------------------------------
# AUTH-L10: translator
# ---------------------------------------------------------------------------


def _translator(tmp_path, **groups) -> Translator:
    for locale, body in groups.items():
        (tmp_path / locale).mkdir(parents=True, exist_ok=True)
        (tmp_path / locale / "messages.py").write_text(f"messages = {body!r}\n")
    return Translator(tmp_path, locale="en", fallback_locale="en")


class TestTranslator:
    def test_longer_placeholder_is_not_corrupted(self, tmp_path):
        t = _translator(tmp_path, en={"a": "Hi :name, full: :name_full", "b": ":name_full only"})
        assert t.get("messages.a", {"name": "Ann", "name_full": "Ann Lee"}) == "Hi Ann, full: Ann Lee"
        assert t.get("messages.a", {"name": "Ann"}) == "Hi Ann, full: :name_full"
        assert t.get("messages.b", {"name": "Ann"}) == ":name_full only"

    def test_substituted_values_are_not_rescanned(self, tmp_path):
        t = _translator(tmp_path, en={"a": ":first and :second"})
        assert t.get("messages.a", {"first": ":second", "second": "SECRET"}) == ":second and SECRET"
        assert t.get("messages.a", {"second": "x", "first": ":second"}) == ":second and x"

    def test_case_variants_still_work(self, tmp_path):
        t = _translator(tmp_path, en={"a": ":name :Name :NAME"})
        assert t.get("messages.a", {"name": "ann"}) == "ann Ann ANN"

    def test_choice_count_placeholder(self, tmp_path):
        t = _translator(tmp_path, en={"apples": "{0} none|{1} one apple|[2,*] :count apples"})
        assert t.choice("messages.apples", 5) == "5 apples"
        assert t.choice("messages.apples", 1) == "one apple"

    def test_set_locale_inside_a_request_does_not_leak(self, tmp_path):
        t = _translator(tmp_path, en={"hi": "Hello"}, fr={"hi": "Bonjour"})
        request_a, request_b = object(), object()
        with patch.object(Translator, "_current_request", return_value=request_a):
            t.set_locale("fr")
            assert t.get_locale() == "fr"
            assert t.get("messages.hi") == "Bonjour"
        with patch.object(Translator, "_current_request", return_value=request_b):
            assert t.get_locale() == "en"
            assert t.get("messages.hi") == "Hello"
        with patch.object(Translator, "_current_request", return_value=None):
            assert t.get_locale() == "en"

    def test_set_locale_outside_a_request_sets_the_default(self, tmp_path):
        t = _translator(tmp_path, en={"hi": "Hello"}, fr={"hi": "Bonjour"})
        with patch.object(Translator, "_current_request", return_value=None):
            t.set_locale("fr")
        with patch.object(Translator, "_current_request", return_value=object()):
            assert t.get("messages.hi") == "Bonjour"
            t.set_default_locale("en")
            assert t.get_locale() == "en"

    def test_concurrent_requests_keep_their_own_locale(self, tmp_path):
        import asyncio

        from hunt.auth.manager import _request_var

        t = _translator(tmp_path, en={"hi": "Hello"}, fr={"hi": "Bonjour"})
        seen: dict[str, str] = {}

        async def handle(name: str, locale: str | None, gate: asyncio.Event, release: asyncio.Event) -> None:
            _request_var.set(object())
            if locale:
                t.set_locale(locale)
            gate.set()
            await release.wait()
            seen[name] = t.get("messages.hi")

        async def main() -> None:
            a_ready, b_ready, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            task_a = asyncio.create_task(handle("a", "fr", a_ready, release))
            await a_ready.wait()
            task_b = asyncio.create_task(handle("b", None, b_ready, release))
            await b_ready.wait()
            release.set()
            await asyncio.gather(task_a, task_b)

        asyncio.run(main())
        assert seen == {"a": "Bonjour", "b": "Hello"}

    def test_cache_is_bounded(self, tmp_path):
        from hunt.translation import translator as mod

        t = _translator(tmp_path, en={"hi": "Hello"})
        for i in range(mod._MAX_CACHE_ENTRIES * 2):
            t.get(f"group{i}.key")
        assert len(t._cache) <= mod._MAX_CACHE_ENTRIES
        assert t.get("messages.hi") == "Hello"


# ---------------------------------------------------------------------------
# AUTH-L11: Str, dd(), Collection.contains(), back()
# ---------------------------------------------------------------------------


class TestStr:
    @pytest.mark.parametrize(("word", "expected"), [("y", "ys"), ("", ""), ("city", "cities"), ("day", "days")])
    def test_plural_short_words(self, word, expected):
        assert Str.plural(word) == expected

    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("status", "status"),
            ("statuses", "status"),
            ("Status", "Status"),
            ("order_status", "order_status"),
            ("order_statuses", "order_status"),
            ("OrderStatuses", "OrderStatus"),
            ("bus", "bus"),
            ("buses", "bus"),
            ("alias", "alias"),
            ("aliases", "alias"),
            ("news", "news"),
            ("series", "series"),
            ("people", "person"),
            ("children", "child"),
            ("posts", "post"),
            ("categories", "category"),
            ("boxes", "box"),
            ("classes", "class"),
            ("files", "file"),
            ("analyses", "analysis"),
            ("taxis", "taxi"),
            ("s", ""),
            ("", ""),
        ],
    )
    def test_singular(self, word, expected):
        assert Str.singular(word) == expected

    def test_table_name_inference_round_trips_status(self):
        assert Str.plural(Str.snake("OrderStatus")) == "order_statuses"
        assert Str.singular("order_statuses") == "order_status"


class TestDd:
    def test_dd_exits_in_a_cli_script(self, capsys):
        from hunt.support.helpers import dd

        with patch("hunt.auth.manager._get_current_request", return_value=None), pytest.raises(SystemExit):
            dd("value")
        assert "value" in capsys.readouterr().out

    def test_dd_does_not_raise_system_exit_during_a_request(self, capsys):
        from hunt.support.helpers import DumpAndDie, dd

        with patch("hunt.auth.manager._get_current_request", return_value=object()):
            with pytest.raises(DumpAndDie) as exc:
                dd("a", {"b": 1})
        assert exc.value.values == ("a", {"b": 1})
        assert isinstance(exc.value, Exception)
        assert not isinstance(exc.value, SystemExit)
        assert "'a'" in capsys.readouterr().out


class TestCollectionContains:
    def test_key_with_none_value_compares_the_key(self):
        items = Collection([{"deleted_at": None}, {"deleted_at": 5}])
        assert items.contains("deleted_at", None) is True
        assert Collection([{"deleted_at": 5}]).contains("deleted_at", None) is False

    def test_other_forms_are_unchanged(self):
        items = Collection([{"id": 1}, {"id": 2}])
        assert items.contains("id", 2) is True
        assert items.contains("id", 3) is False
        assert Collection([1, 2, 3]).contains(2) is True
        assert Collection([1, 2, 3]).contains(9) is False
        assert Collection([1, 2, 3]).contains(lambda x: x > 2) is True
        assert Collection([None]).contains(None) is True


class TestBack:
    def test_back_delegates_to_fluent_redirect(self):
        from hunt.http.response import FluentRedirect
        from hunt.support.helpers import back

        sentinel = object()
        with patch.object(FluentRedirect, "back", return_value=sentinel) as fluent_back:
            assert back("/home") is sentinel
        fluent_back.assert_called_once_with("/home")

    def test_back_without_request_uses_default(self):
        from hunt.support.helpers import back

        with patch("hunt.auth.manager._get_current_request", return_value=None):
            response = back("/fallback")
        assert response.status == 302
        headers = {str(k).lower(): v for k, v in dict(response._headers).items()}
        assert headers["location"] == "/fallback"


# ---------------------------------------------------------------------------
# HTTP-11 (container half): string annotations
# ---------------------------------------------------------------------------


class Mailer:
    pass


class Newsletter:
    def __init__(self, mailer: Mailer, retries: int = 3) -> None:
        self.mailer = mailer
        self.retries = retries


class Optionalish:
    def __init__(self, mailer: Mailer, amount: Decimal | None = None) -> None:
        self.mailer = mailer
        self.amount = amount


class Unresolvable:
    def __init__(self, amount: Decimal) -> None:
        self.amount = amount


def send(mailer: Mailer, subject: str = "hi") -> tuple:
    return mailer, subject


class TestContainerStringAnnotations:
    """This module uses ``from __future__ import annotations``: every annotation is a string."""

    def setup_method(self):
        self.container = Container()
        self.container.singleton(Mailer)

    def test_constructor_dependency_is_injected(self):
        assert isinstance(Newsletter.__init__.__annotations__["mailer"], str)
        newsletter = self.container.make(Newsletter)
        assert isinstance(newsletter.mailer, Mailer)
        assert newsletter.mailer is self.container.make(Mailer)
        assert newsletter.retries == 3

    def test_call_injects_dependencies(self):
        mailer, subject = self.container.call(send)
        assert mailer is self.container.make(Mailer)
        assert subject == "hi"
        assert self.container.call(send, {"subject": "yo"})[1] == "yo"

    def test_unresolvable_optional_annotation_does_not_hide_the_others(self):
        obj = self.container.make(Optionalish)
        assert isinstance(obj.mailer, Mailer)
        assert obj.amount is None

    def test_unresolvable_required_parameter_raises_clear_error(self):
        with pytest.raises(BindingResolutionError, match="amount"):
            self.container.make(Unresolvable)
        assert self.container.make(Unresolvable, {"amount": 5}).amount == 5

    def test_bound_factory_with_string_annotations(self):
        def factory(mailer: Mailer) -> Newsletter:
            return Newsletter(mailer, retries=9)

        self.container.bind("newsletter", factory)
        made = self.container.make("newsletter")
        assert isinstance(made.mailer, Mailer)
        assert made.retries == 9

    def test_var_args_are_ignored(self):
        def flexible(mailer: Mailer, *args: int, **kwargs: str) -> Mailer:
            return mailer

        assert self.container.call(flexible) is self.container.make(Mailer)
