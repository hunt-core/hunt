"""Regression tests for the validation findings (VAL-01..06, VAL-L1..L5)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hunt.http.request import Request, UploadedFile
from hunt.validation.form_request import FormRequest, request_data
from hunt.validation.rules import RULES
from hunt.validation.validator import ValidationException, Validator

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def passes(data: dict, rules: dict) -> bool:
    return Validator.make(data, rules).passes()


def errors(data: dict, rules: dict) -> dict:
    v = Validator.make(data, rules)
    v.passes()
    return v.errors()._errors


def multipart(parts: list[tuple[str, str | None, bytes]]) -> Request:
    """Build a multipart request from (name, filename-or-None, content) parts."""
    boundary = "fixboundary"
    body = b""
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode()
        if filename is not None:
            body += b"Content-Type: image/png\r\n"
        body += b"\r\n" + content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
    }
    return Request(scope, body)


# ---------------------------------------------------------------------------
# VAL-01: min / max / size on numeric form input
# ---------------------------------------------------------------------------


class TestNumericBounds:
    @pytest.mark.parametrize("rule", ["required|integer|max:100", "required|numeric|max:100"])
    def test_string_number_above_max_fails(self, rule):
        assert not passes({"qty": "999999999"}, {"qty": rule})
        assert passes({"qty": "100"}, {"qty": rule})

    def test_string_number_below_min_fails(self):
        assert not passes({"qty": "-5"}, {"qty": "integer|min:1"})
        assert not passes({"qty": "0"}, {"qty": "integer|min:1"})

    def test_string_number_inside_bounds_passes(self):
        # Used to fail with "at least 10 characters".
        assert passes({"qty": "50"}, {"qty": "integer|min:10"})
        assert passes({"price": "19.99"}, {"price": "numeric|min:0.5|max:20"})

    def test_same_result_for_json_integer_and_form_string(self):
        for qty in (500, "500"):
            assert not passes({"qty": qty}, {"qty": "integer|max:100"})
        for qty in (50, "50"):
            assert passes({"qty": qty}, {"qty": "integer|max:100"})

    def test_message_talks_about_value_not_characters(self):
        msg = errors({"qty": "5"}, {"qty": "integer|min:10"})["qty"][0]
        assert "characters" not in msg
        assert "at least 10" in msg

    def test_plain_string_still_uses_length(self):
        assert passes({"name": "999999999"}, {"name": "string|max:20"})
        assert not passes({"name": "x" * 21}, {"name": "string|max:20"})
        assert not passes({"pw": "ab"}, {"pw": "required|min:8"})

    def test_bool_is_not_compared_as_integer(self):
        assert passes({"flag": True}, {"flag": "boolean|max:0"})
        assert not passes({"flag": True}, {"flag": "integer"})

    def test_size_for_numbers_lists_and_strings(self):
        assert passes({"n": "5"}, {"n": "integer|size:5"})
        assert not passes({"n": "6"}, {"n": "integer|size:5"})
        assert passes({"tags": ["a", "b"]}, {"tags": "array|size:2"})
        assert not passes({"tags": ["a"]}, {"tags": "array|size:2"})
        assert passes({"code": "abcd"}, {"code": "size:4"})
        assert not passes({"code": "abc"}, {"code": "size:4"})

    def test_between(self):
        assert passes({"n": "5"}, {"n": "integer|between:1,10"})
        assert not passes({"n": "11"}, {"n": "integer|between:1,10"})
        assert passes({"s": "abc"}, {"s": "between:1,3"})
        assert not passes({"s": "abcd"}, {"s": "between:1,3"})

    def test_file_size_in_kilobytes(self):
        big = UploadedFile("a.png", "image/png", PNG + b"\x00" * 3000)
        assert not passes({"f": big}, {"f": "max:1"})
        assert passes({"f": big}, {"f": "max:10"})


# ---------------------------------------------------------------------------
# VAL-02: thresholds are literals, NaN / inf never pass
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_client_cannot_override_numeric_threshold(self):
        assert not passes({"amount": "-50", "0": "-100"}, {"amount": "required|numeric|gt:0"})
        assert not passes({"amount": "500", "100": "1000"}, {"amount": "numeric|lte:100"})

    def test_client_cannot_override_date_threshold(self):
        data = {"d": "1999-01-01", "2020-01-01": "1990-01-01"}
        assert not passes(data, {"d": "after:2020-01-01"})
        data = {"d": "2030-01-01", "2020-01-01": "2040-01-01"}
        assert not passes(data, {"d": "before:2020-01-01"})

    @pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "Infinity", float("nan"), float("inf")])
    def test_non_finite_values_fail(self, value):
        assert not passes({"amount": value}, {"amount": "numeric"})
        assert not passes({"amount": value}, {"amount": "gt:0"})
        assert not passes({"amount": value}, {"amount": "lt:100"})
        assert not passes({"amount": value}, {"amount": "gte:0"})
        assert not passes({"amount": value}, {"amount": "lte:100"})

    def test_field_reference_still_works(self):
        assert passes({"low": "1", "high": "5"}, {"high": "numeric|gt:low"})
        assert not passes({"low": "9", "high": "5"}, {"high": "numeric|gt:low"})
        assert not passes({"high": "5"}, {"high": "numeric|gt:low"})  # unresolvable reference
        assert passes({"start": "2024-01-01", "end": "2024-02-01"}, {"end": "after:start"})
        assert not passes({"start": "2024-03-01", "end": "2024-02-01"}, {"end": "after:start"})

    @pytest.mark.parametrize("value", ["12", "-3", "+7", 5])
    def test_integer_accepts(self, value):
        assert passes({"n": value}, {"n": "integer"})

    @pytest.mark.parametrize("value", [" 5", "5 ", "1_000", "5.0", "١٢", "1e3", "", True, 5.5, "abc"])
    def test_integer_rejects(self, value):
        assert not passes({"n": value}, {"n": "required|integer"})

    def test_numeric_rejects_underscores_and_words(self):
        assert not passes({"n": "1_000"}, {"n": "numeric"})
        assert passes({"n": "1.5e3"}, {"n": "numeric"})


# ---------------------------------------------------------------------------
# VAL-03: file rules see uploaded files
# ---------------------------------------------------------------------------


class TestFileRules:
    def _controller(self):
        from hunt.http.controller import Controller

        return Controller()

    def test_valid_file_passes_required_image(self):
        request = multipart([("title", None, b"hi"), ("avatar", "a.png", PNG)])
        out = self._controller().validate(request, {"title": "required", "avatar": "required|image"})
        assert out["title"] == "hi"
        assert isinstance(out["avatar"], UploadedFile)

    def test_oversize_file_is_rejected(self):
        request = multipart([("avatar", "a.png", PNG + b"\x00" * 4096)])
        with pytest.raises(ValidationException) as exc:
            self._controller().validate(request, {"avatar": "image|max_file_size:1"})
        assert "avatar" in exc.value.errors

    def test_wrong_type_is_rejected(self):
        request = multipart([("avatar", "a.png", b"#!/bin/sh\necho pwned\n")])
        with pytest.raises(ValidationException) as exc:
            self._controller().validate(request, {"avatar": "image"})
        assert "avatar" in exc.value.errors

    def test_missing_file_fails_required(self):
        request = multipart([("title", None, b"hi")])
        with pytest.raises(ValidationException):
            self._controller().validate(request, {"avatar": "required|image"})

    def test_empty_file_input_counts_as_no_file(self):
        request = multipart([("avatar", "", b"")])
        assert "avatar" not in request_data(request)
        assert self._controller().validate(request, {"avatar": "nullable|image"}) == {}
        with pytest.raises(ValidationException):
            self._controller().validate(request, {"avatar": "required|image"})

    def test_text_field_cannot_satisfy_file_rules(self):
        request = multipart([("avatar", None, b"not a file")])
        for rule in ("file", "image", "mimes:png", "max_file_size:10"):
            with pytest.raises(ValidationException):
                self._controller().validate(request, {"avatar": rule})

    def test_form_request_validated_includes_files(self):
        class AvatarRequest(FormRequest):
            def rules(self) -> dict:
                return {"avatar": "required|image|max_file_size:100"}

        form = AvatarRequest(multipart([("avatar", "a.png", PNG), ("is_admin", None, b"1")]))
        validated = form.validated()
        assert list(validated) == ["avatar"]
        assert validated["avatar"].filename == "a.png"

    def test_form_request_rejects_bad_upload(self):
        class AvatarRequest(FormRequest):
            def rules(self) -> dict:
                return {"avatar": "required|image"}

        with pytest.raises(ValidationException):
            AvatarRequest(multipart([("avatar", "a.png", b"plain text")])).validated()

    def test_multiple_files_validate_with_wildcard(self):
        request = multipart([("photos", "a.png", PNG), ("photos", "b.png", b"nope")])
        with pytest.raises(ValidationException) as exc:
            self._controller().validate(request, {"photos.*": "image"})
        assert "photos.1" in exc.value.errors

    @pytest.mark.parametrize(
        ("filename", "content", "ext"),
        [
            ("notes.txt", b"hello world\n", "txt"),
            ("data.csv", b"a,b\n1,2\n", "csv"),
            ("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32, "mp4"),
            ("song.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x21" + b"\x00" * 32, "mp3"),
            ("song.mp3", b"\xff\xfb\x90\x00" + b"\x00" * 32, "mp3"),
            ("report.docx", b"PK\x03\x04" + b"\x00" * 32, "docx"),
            ("icon.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "svg"),
            ("a.png", PNG, "png"),
        ],
    )
    def test_mimes_accepts_real_files(self, filename, content, ext):
        upload = UploadedFile(filename, "application/octet-stream", content)
        assert passes({"f": upload}, {"f": f"mimes:{ext}"})

    @pytest.mark.parametrize(
        ("filename", "content", "ext"),
        [
            ("notes.txt", PNG, "txt"),  # binary content
            ("notes.png", b"hello world\n", "txt"),  # wrong extension
            ("evil.txt", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 16, "txt"),  # NUL bytes
            ("report.docx", b"hello", "docx"),
            ("report.zip", b"PK\x03\x04" + b"\x00" * 32, "docx"),
            ("clip.mp4", b"not a video", "mp4"),
            ("a.png", b"\xff\xd8\xff\xe0" + b"\x00" * 16, "png"),
        ],
    )
    def test_mimes_rejects_mismatches(self, filename, content, ext):
        upload = UploadedFile(filename, "text/plain", content)
        assert not passes({"f": upload}, {"f": f"mimes:{ext}"})

    def test_mimes_unknown_extension_raises_when_built(self):
        with pytest.raises(ValueError, match="unsupported file extension"):
            Validator.make({}, {"f": "mimes:exe"})


# ---------------------------------------------------------------------------
# VAL-04: rule parameters containing ":", "," and "|"
# ---------------------------------------------------------------------------


class TestParameterParsing:
    def test_date_format_keeps_colons(self):
        assert passes({"t": "14:30"}, {"t": "date_format:%H:%M"})
        assert not passes({"t": "14"}, {"t": "date_format:%H:%M"})
        assert passes({"t": "2024-01-02 03:04:05"}, {"t": "date_format:%Y-%m-%d %H:%M:%S"})

    def test_regex_keeps_commas(self):
        assert passes({"n": "123"}, {"n": r"regex:^\d{2,4}$"})
        assert not passes({"n": "1"}, {"n": r"regex:^\d{2,4}$"})
        assert not passes({"n": "12345"}, {"n": r"regex:^\d{2,4}$"})

    def test_regex_keeps_pipes_in_string_form(self):
        assert passes({"v": "a"}, {"v": "required|regex:^(a|b)$"})
        assert passes({"v": "b"}, {"v": "required|regex:^(a|b)$"})
        assert not passes({"v": "c"}, {"v": "required|regex:^(a|b)$"})

    def test_regex_in_list_form_can_be_followed_by_other_rules(self):
        rules = {"v": ["required", "regex:^(a|b)+$", "max:2"]}
        assert passes({"v": "ab"}, rules)
        assert not passes({"v": "aba"}, rules)
        assert not passes({"v": "c"}, rules)

    def test_regex_with_colon(self):
        assert passes({"v": "key:value"}, {"v": r"regex:^\w+:\w+$"})

    def test_not_regex(self):
        assert passes({"v": "hello"}, {"v": r"not_regex:[<>|]"})
        assert not passes({"v": "a|b"}, {"v": r"not_regex:[<>|]"})

    def test_invalid_pattern_raises_when_built(self):
        with pytest.raises(ValueError, match="invalid regular expression"):
            Validator.make({"v": "x"}, {"v": "regex:^(a"})

    def test_bad_numeric_parameter_raises_when_built(self):
        with pytest.raises(ValueError, match="not a number"):
            Validator.make({}, {"v": "max:lots"})


# ---------------------------------------------------------------------------
# VAL-05: unknown rules
# ---------------------------------------------------------------------------


class TestUnknownRules:
    def test_typo_raises(self):
        with pytest.raises(ValueError, match="Unknown validation rule 'requried'"):
            Validator.make({}, {"email": "requried|emial"})

    def test_typo_in_list_form_raises(self):
        with pytest.raises(ValueError, match="emial"):
            Validator.make({}, {"email": ["required", "emial"]})

    def test_registry_stays_extensible(self):
        def _even(value, params, data):  # classic three-argument custom rule
            return None if int(value) % 2 == 0 else "The field must be even."

        with patch.dict(RULES, {"even": _even}):
            assert passes({"n": "4"}, {"n": "even"})
            assert errors({"n": "3"}, {"n": "even"}) == {"n": ["The n field must be even."]}
        with pytest.raises(ValueError):
            Validator.make({}, {"n": "even"})

    def test_custom_rule_can_receive_context(self):
        seen = {}

        def _probe(value, params, data, ctx):
            seen["field"] = ctx.field
            seen["numeric"] = ctx.numeric
            return None

        with patch.dict(RULES, {"probe": _probe}):
            assert passes({"a": {"b": "1"}}, {"a.b": "integer|probe"})
        assert seen == {"field": "a.b", "numeric": True}

    @pytest.mark.parametrize(
        ("rule", "good", "bad"),
        [
            ("alpha", "abc", "abc1"),
            ("alpha_num", "abc123", "abc-123"),
            ("alpha_dash", "abc-1_2", "abc 12"),
            ("digits:4", "0123", "123"),
            ("digits:4", "0123", "12a4"),
            ("digits_between:2,4", "123", "12345"),
            ("accepted", "on", "off"),
            ("declined", "no", "yes"),
            ("same:other", "x", "y"),
            ("filled", "x", "  "),
        ],
    )
    def test_common_rules_exist(self, rule, good, bad):
        assert passes({"v": good, "other": "x"}, {"v": rule})
        assert not passes({"v": bad, "other": "x"}, {"v": rule})

    def test_accepted_fails_when_missing(self):
        assert not passes({}, {"terms": "accepted"})
        assert passes({"terms": True}, {"terms": "accepted"})

    def test_file_rule(self):
        assert passes({"f": UploadedFile("a.png", "image/png", PNG)}, {"f": "file"})
        assert not passes({"f": "a.png"}, {"f": "file"})


# ---------------------------------------------------------------------------
# VAL-06: only validated data is returned
# ---------------------------------------------------------------------------


class TestValidatedOutput:
    def test_validate_returns_only_ruled_keys(self):
        out = Validator.make({"name": "A", "is_admin": True}, {"name": "required"}).validate()
        assert out == {"name": "A"}

    def test_nested_rule_returns_only_that_leaf(self):
        data = {"address": {"city": "Paris", "is_admin": True}}
        assert Validator.make(data, {"address.city": "required"}).validate() == {"address": {"city": "Paris"}}

    def test_parent_with_child_rules_does_not_leak_siblings(self):
        data = {"address": {"city": "Paris", "is_admin": True}}
        rules = {"address": "required", "address.city": "required|string"}
        assert Validator.make(data, rules).validate() == {"address": {"city": "Paris"}}

    def test_parent_without_child_rules_is_copied_whole(self):
        data = {"tags": ["a", "b"], "x": 1}
        assert Validator.make(data, {"tags": "array"}).validate() == {"tags": ["a", "b"]}

    def test_wildcards_return_only_validated_leaves(self):
        data = {"items": [{"name": "a", "is_admin": 1}, {"name": "b", "price": 5}], "extra": 1}
        out = Validator.make(data, {"items": "required|array", "items.*.name": "required"}).validate()
        assert out == {"items": [{"name": "a"}, {"name": "b"}]}

    def test_wildcard_of_scalars(self):
        out = Validator.make({"ids": ["1", "2"], "x": 1}, {"ids.*": "integer"}).validate()
        assert out == {"ids": ["1", "2"]}

    def test_empty_list_parent_is_kept(self):
        out = Validator.make({"items": []}, {"items": "array", "items.*.name": "required"}).validate()
        assert out == {"items": []}

    def test_absent_optional_key_is_not_invented(self):
        assert Validator.make({"a": "1"}, {"a": "required", "b": "string"}).validate() == {"a": "1"}

    def test_wildcard_parent_that_is_not_a_list_fails(self):
        errs = errors({"items": {"name": "a"}}, {"items.*.name": "required"})
        assert "items" in errs
        assert "array" in errs["items"][0]

    def test_wildcard_parent_string_fails(self):
        assert not passes({"items": "abc"}, {"items.*": "integer"})

    def test_missing_wildcard_parent_fails_required(self):
        errs = errors({}, {"items.*.name": "required"})
        assert errs == {"items": ["The items field is required."]}

    def test_missing_wildcard_parent_ok_when_not_required(self):
        assert passes({}, {"items.*.name": "string"})
        assert passes({}, {"items.*.name": "sometimes|required"})

    def test_controller_validate_returns_only_ruled_keys(self):
        from hunt.http.controller import Controller

        class _Req:
            def all(self):
                return {"title": "T", "is_admin": "1"}

            def header(self, name, default=None):
                return "application/x-www-form-urlencoded"

        assert Controller().validate(_Req(), {"title": "required"}) == {"title": "T"}

    def test_form_request_nested_validated(self):
        class _Req:
            def all(self):
                return {"address": {"city": "Paris", "is_admin": True}, "other": 1}

            def header(self, name, default=None):
                return "application/json"

        class AddressRequest(FormRequest):
            def rules(self) -> dict:
                return {"address.city": "required"}

        assert AddressRequest(_Req()).validated() == {"address": {"city": "Paris"}}

    def test_custom_message_for_wildcard_pattern(self):
        v = Validator.make({"items": [{}]}, {"items.*.name": "required"}).messages(
            {"items.*.name.required": "Each item needs a name."}
        )
        assert v.fails()
        assert v.errors().first("items.0.name") == "Each item needs a name."


# ---------------------------------------------------------------------------
# Low-severity items
# ---------------------------------------------------------------------------


class TestLowSeverity:
    @pytest.mark.parametrize("value", ["a@b.co\n", "a@b.co\r\nBcc: x@y.zz", "a@b.co ", " a@b.co"])
    def test_email_rejects_trailing_newline_and_whitespace(self, value):  # VAL-L1
        assert not passes({"email": value}, {"email": "email"})
        assert passes({"email": "a@b.co"}, {"email": "email"})

    @pytest.mark.parametrize("value", ["   ", "\t\n", [], {}])
    def test_required_rejects_blank_and_empty(self, value):  # VAL-L2
        assert not passes({"v": value}, {"v": "required"})

    @pytest.mark.parametrize("value", [0, False, "0", ["a"]])
    def test_required_accepts_falsy_values(self, value):
        assert passes({"v": value}, {"v": "required"})

    @pytest.mark.parametrize(
        "value",
        [
            "http://exa mple.com",
            "http://example.com/<script>alert(1)</script>",
            "http://example.com/\n",
            'http://example.com/"onmouseover="x',
            "javascript:alert(1)",
            "http://",
            "http://-bad-.com",
            "http://example.com:99999999",
        ],
    )
    def test_url_rejects(self, value):  # VAL-L2
        assert not passes({"u": value}, {"u": "url"})

    @pytest.mark.parametrize(
        "value",
        ["https://example.com", "http://example.com:8080/a/b?c=d&e=f#g", "http://127.0.0.1/x", "http://localhost/"],
    )
    def test_url_accepts(self, value):
        assert passes({"u": value}, {"u": "url"})

    def test_confirmed_on_nested_field(self):  # VAL-L3
        data = {"user": {"password": "s3cret", "password_confirmation": "s3cret"}}
        assert passes(data, {"user.password": "required|confirmed"})
        data["user"]["password_confirmation"] = "other"
        assert not passes(data, {"user.password": "required|confirmed"})

    def test_confirmed_top_level_unchanged(self):
        assert passes({"password": "a", "password_confirmation": "a"}, {"password": "confirmed"})
        assert not passes({"password": "a"}, {"password": "confirmed"})

    def test_required_with_resolves_siblings(self):  # VAL-L3
        rules = {"address.zip": "required_with:city"}
        assert not passes({"address": {"city": "Paris"}}, rules)
        assert passes({"address": {"city": "Paris", "zip": "75001"}}, rules)
        assert passes({"address": {}}, rules)

    def test_required_if_resolves_siblings_and_absolute_paths(self):
        rules = {"items.*.reason": "required_if:status,rejected"}
        assert not passes({"items": [{"status": "rejected"}]}, rules)
        assert passes({"items": [{"status": "ok"}]}, rules)
        assert not passes({"kind": "b", "detail": {}}, {"detail.note": "required_if:kind,b"})

    @pytest.mark.parametrize("value", [["a"], {"$ne": None}, {"a": 1}])
    def test_unique_and_exists_reject_non_scalars_without_query(self, value):  # VAL-L4
        with patch("hunt.database.connection.connection", side_effect=AssertionError("no query expected")):
            assert not passes({"email": value}, {"email": "unique:users,email"})
            assert not passes({"email": value}, {"email": "exists:users,email"})

    def test_in_message_does_not_list_allowed_values(self):  # VAL-L5
        msg = errors({"role": "root"}, {"role": "in:user,admin,secret_role"})["role"][0]
        assert "secret_role" not in msg
        assert "admin" not in msg
