from __future__ import annotations

from typing import TYPE_CHECKING

from hunt.validation.validator import Validator

if TYPE_CHECKING:
    from hunt.http.request import Request


class FormRequest:
    def __init__(self, request: Request) -> None:
        self.request = request
        self._validated_data: dict | None = None

    def authorize(self) -> bool:
        return True

    def rules(self) -> dict:
        return {}

    def messages(self) -> dict:
        return {}

    def validated(self) -> dict:
        if self._validated_data is None:
            self._validated_data = self._run_validation()
        return self._validated_data

    def _run_validation(self) -> dict:
        if not self.authorize():
            from hunt.http.response import HttpException

            raise HttpException(403, "Forbidden")
        v = Validator.make(self.request.all(), self.rules())
        if self.messages():
            v.messages(self.messages())
        return v.validate()
