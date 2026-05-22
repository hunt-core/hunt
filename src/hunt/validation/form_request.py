from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hunt.validation.validator import Validator

if TYPE_CHECKING:
    from hunt.http.request import Request


class FormRequest:
    def __init__(self, request: Request) -> None:
        self.request = request
        self._validated_data: dict | None = None

    # ------------------------------------------------------------------
    # Override these in subclasses
    # ------------------------------------------------------------------

    def authorize(self) -> bool:
        return True

    def rules(self) -> dict:
        return {}

    def messages(self) -> dict:
        return {}

    def after_validation(self, validator: Validator) -> None:
        """Hook called after validation passes. Override to add extra checks."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validated(self) -> dict:
        """Return only the fields declared in rules(), after validation passes."""
        if self._validated_data is None:
            self._validated_data = self._run_validation()
        return self._validated_data

    def input(self, key: str, default: Any = None) -> Any:
        """Read a value from the underlying request."""
        return self.request.input(key, default)

    def all(self) -> dict:
        """Return all raw request input (unvalidated)."""
        return self.request.all()

    def file(self, key: str) -> Any:
        """Return an uploaded file from the request."""
        return self.request.file(key)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_validation(self) -> dict:
        if not self.authorize():
            from hunt.http.response import HttpException

            raise HttpException(403, "Forbidden")

        v = Validator.make(self.request.all(), self.rules())
        if self.messages():
            v.messages(self.messages())

        all_data = v.validate()

        self.after_validation(v)

        # Return only top-level keys declared in rules() — mass-assignment safety.
        # 'address.city' → top key 'address'; 'items.*.name' → top key 'items'.
        safe_keys: set[str] = set()
        for field in self.rules():
            top = field.split(".")[0].rstrip("*").strip()
            if top:
                safe_keys.add(top)

        return {k: v for k, v in all_data.items() if k in safe_keys}
