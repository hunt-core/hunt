import pytest
from hunt.validation.validator import Validator, ValidationException


def test_required_passes():
    v = Validator.make({"name": "Alice"}, {"name": "required"})
    result = v.validate()
    assert result["name"] == "Alice"


def test_required_fails():
    v = Validator.make({}, {"name": "required"})
    with pytest.raises(ValidationException) as exc:
        v.validate()
    assert "name" in exc.value.errors


def test_email_rule():
    v = Validator.make({"email": "not-an-email"}, {"email": "required|email"})
    with pytest.raises(ValidationException) as exc:
        v.validate()
    assert "email" in exc.value.errors


def test_min_max():
    v = Validator.make({"pw": "ab"}, {"pw": "required|min:8"})
    assert v.fails()

    v2 = Validator.make({"pw": "longenough"}, {"pw": "required|min:8|max:20"})
    assert v2.passes()


def test_in_rule():
    v = Validator.make({"role": "admin"}, {"role": "in:user,admin,moderator"})
    assert v.passes()

    v2 = Validator.make({"role": "superuser"}, {"role": "in:user,admin,moderator"})
    assert v2.fails()


def test_multiple_errors():
    v = Validator.make({}, {"name": "required", "email": "required|email"})
    assert v.fails()
    errors = v.errors()
    assert errors.has("name")
    assert errors.has("email")
