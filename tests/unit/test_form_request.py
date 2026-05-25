"""Tests for FormRequest (M22)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from hunt.validation.form_request import FormRequest
from hunt.validation.validator import ValidationException, Validator

# ---------------------------------------------------------------------------
# Minimal Request stub — avoids the ASGI machinery
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, data: dict) -> None:
        self._data = data

    def all(self) -> dict:
        return dict(self._data)

    def input(self, key: str, default=None):
        return self._data.get(key, default)

    def file(self, key: str):
        return self._data.get(key)


def _make_form(cls, data: dict):
    return cls(_FakeRequest(data))


# ---------------------------------------------------------------------------
# Basic FormRequest subclasses for testing
# ---------------------------------------------------------------------------


class CreatePostRequest(FormRequest):
    def rules(self) -> dict:
        return {
            "title": "required|min:3",
            "body": "required",
        }


class AdminOnlyRequest(FormRequest):
    def authorize(self) -> bool:
        return False

    def rules(self) -> dict:
        return {"name": "required"}


class CustomMessagesRequest(FormRequest):
    def rules(self) -> dict:
        return {"email": "required|email"}

    def messages(self) -> dict:
        return {"email.required": "Please provide your email."}


class AfterValidationRequest(FormRequest):
    after_called: bool = False

    def rules(self) -> dict:
        return {"title": "required"}

    def after_validation(self, validator: Validator) -> None:
        AfterValidationRequest.after_called = True


class NestedFieldRequest(FormRequest):
    def rules(self) -> dict:
        return {"address.city": "required", "address.zip": "required"}


class ExtraFieldRequest(FormRequest):
    def rules(self) -> dict:
        return {"title": "required"}


# ---------------------------------------------------------------------------
# validated() returns only declared fields
# ---------------------------------------------------------------------------


class TestValidatedFiltersFields:
    def test_returns_declared_fields_only(self):
        req = _make_form(CreatePostRequest, {"title": "Hello", "body": "World", "extra": "boom"})
        result = req.validated()
        assert "title" in result
        assert "body" in result
        assert "extra" not in result

    def test_returns_correct_values(self):
        req = _make_form(CreatePostRequest, {"title": "Hello world", "body": "Content"})
        result = req.validated()
        assert result["title"] == "Hello world"
        assert result["body"] == "Content"

    def test_nested_rule_top_key_returned(self):
        req = _make_form(NestedFieldRequest, {"address": {"city": "NYC", "zip": "10001"}})
        result = req.validated()
        assert "address" in result
        assert result["address"]["city"] == "NYC"

    def test_extra_fields_stripped(self):
        req = _make_form(ExtraFieldRequest, {"title": "Hi", "secret": "hidden", "csrf": "token"})
        result = req.validated()
        assert set(result.keys()) == {"title"}

    def test_validated_cached(self):
        req = _make_form(CreatePostRequest, {"title": "Hi there", "body": "ok"})
        first = req.validated()
        second = req.validated()
        assert first is second


# ---------------------------------------------------------------------------
# Validation failure
# ---------------------------------------------------------------------------


class TestValidationFailure:
    def test_raises_on_missing_required(self):
        req = _make_form(CreatePostRequest, {"title": ""})
        with pytest.raises(ValidationException) as exc:
            req.validated()
        assert "title" in exc.value.errors or "body" in exc.value.errors

    def test_raises_on_min_violation(self):
        req = _make_form(CreatePostRequest, {"title": "Hi", "body": "ok"})
        with pytest.raises(ValidationException) as exc:
            req.validated()
        assert "title" in exc.value.errors

    def test_empty_rules_passes_with_no_data(self):
        class EmptyRequest(FormRequest):
            def rules(self):
                return {}

        req = _make_form(EmptyRequest, {})
        assert req.validated() == {}


# ---------------------------------------------------------------------------
# authorize()
# ---------------------------------------------------------------------------


class TestAuthorize:
    def test_forbidden_raises_http_exception(self):
        from hunt.http.response import HttpException

        req = _make_form(AdminOnlyRequest, {"name": "Alice"})
        with pytest.raises(HttpException) as exc:
            req.validated()
        assert exc.value.status == 403


# ---------------------------------------------------------------------------
# messages()
# ---------------------------------------------------------------------------


class TestCustomMessages:
    def test_custom_message_used(self):
        req = _make_form(CustomMessagesRequest, {})
        with pytest.raises(ValidationException) as exc:
            req.validated()
        msgs = exc.value.all()
        assert any("Please provide your email" in m for m in msgs)


# ---------------------------------------------------------------------------
# after_validation hook
# ---------------------------------------------------------------------------


class TestAfterValidation:
    def test_hook_called_on_success(self):
        AfterValidationRequest.after_called = False
        req = _make_form(AfterValidationRequest, {"title": "Hello"})
        req.validated()
        assert AfterValidationRequest.after_called is True

    def test_hook_not_called_on_failure(self):
        AfterValidationRequest.after_called = False
        req = _make_form(AfterValidationRequest, {})
        with pytest.raises(ValidationException):
            req.validated()
        assert AfterValidationRequest.after_called is False


# ---------------------------------------------------------------------------
# Delegation helpers: input() / all() / file()
# ---------------------------------------------------------------------------


class TestDelegationHelpers:
    def test_input_delegates_to_request(self):
        req = _make_form(CreatePostRequest, {"title": "hi", "body": "ok"})
        assert req.input("title") == "hi"
        assert req.input("missing", "default") == "default"

    def test_all_returns_all_raw_data(self):
        req = _make_form(CreatePostRequest, {"title": "hi", "body": "ok", "extra": "x"})
        data = req.all()
        assert data["extra"] == "x"


# ---------------------------------------------------------------------------
# @old directive
# ---------------------------------------------------------------------------


class TestOldDirective:
    def test_old_single_arg(self):
        from hunt.view.directives import preprocess

        result = preprocess("@old('title')")
        assert "{{ old('title') }}" in result

    def test_old_with_default(self):
        from hunt.view.directives import preprocess

        result = preprocess("@old('email', '')")
        assert "old('email', '')" in result

    def test_old_in_input_value(self):
        from hunt.view.directives import preprocess

        result = preprocess("<input value=\"@old('title')\">")
        assert "old('title')" in result


# ---------------------------------------------------------------------------
# @errors directive
# ---------------------------------------------------------------------------


class TestErrorsDirective:
    def test_errors_expands(self):
        from hunt.view.directives import preprocess

        result = preprocess("@errors")
        assert "errors" in result
        assert "{% if" in result
        assert "<ul" in result

    def test_errors_multiple_occurrences(self):
        from hunt.view.directives import preprocess

        result = preprocess("@errors\n@errors")
        assert result.count("{% if errors") == 2

    def test_errors_not_confused_with_error_directive(self):
        from hunt.view.directives import preprocess

        result = preprocess("@error('title')msg@enderror")
        assert "{% if errors is defined and 'title' in errors %}" in result


# ---------------------------------------------------------------------------
# make:form command
# ---------------------------------------------------------------------------


class TestMakeForm:
    def test_creates_file(self, tmp_path, monkeypatch):
        from hunt.console.commands.make.request import make_form_command

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(make_form_command, ["CreatePost"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "app" / "requests" / "create_post_request.py").exists()

    def test_appends_request_suffix(self, tmp_path, monkeypatch):
        from hunt.console.commands.make.request import make_form_command

        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(make_form_command, ["CreatePost"])
        src = (tmp_path / "app" / "requests" / "create_post_request.py").read_text()
        assert "class CreatePostRequest(FormRequest)" in src

    def test_no_duplicate_suffix(self, tmp_path, monkeypatch):
        from hunt.console.commands.make.request import make_form_command

        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(make_form_command, ["CreatePostRequest"])
        assert (tmp_path / "app" / "requests" / "create_post_request.py").exists()

    def test_has_rules_method(self, tmp_path, monkeypatch):
        from hunt.console.commands.make.request import make_form_command

        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(make_form_command, ["StoreComment"])
        src = (tmp_path / "app" / "requests" / "store_comment_request.py").read_text()
        assert "def rules" in src

    def test_no_overwrite(self, tmp_path, monkeypatch):
        from hunt.console.commands.make.request import make_form_command

        monkeypatch.chdir(tmp_path)
        (tmp_path / "app" / "requests").mkdir(parents=True)
        (tmp_path / "app" / "requests" / "create_post_request.py").write_text("# original\n")
        result = CliRunner().invoke(make_form_command, ["CreatePost"])
        assert "Already exists" in result.output
        assert (tmp_path / "app" / "requests" / "create_post_request.py").read_text() == "# original\n"
