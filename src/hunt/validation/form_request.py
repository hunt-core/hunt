from __future__ import annotations

from typing import Any, TYPE_CHECKING

from hunt.validation.validator import ValidationException, Validator

if TYPE_CHECKING:
    from hunt.http.request import Request


class FormRequest:
    def __init__(self, request: "Request") -> None:
        self.request = request
        self._validator: Validator | None = None

    def authorize(self) -> bool:
        return True

    def rules(self) -> dict:
        return {}

    def messages(self) -> dict:
        return {}

    def validated(self) -> dict:
        return self._run_validation()

    def _run_validation(self) -> dict:
        if not self.authorize():
            from hunt.http.response import HttpException
            raise HttpException(403, "Forbidden")

        v = Validator.make(self.request.all(), self.rules())
        if self.messages():
            v.messages(self.messages())
        return v.validate()

    @classmethod
    def from_request(cls, request: "Request") -> "FormRequest":
        instance = cls(request)
        instance._run_validation()
        return instance
