from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hunt.validation.validator import Validator

if TYPE_CHECKING:
    from hunt.http.request import Request


def request_data(request: Any) -> dict:
    """Everything a request submitted that rules may refer to: input plus uploaded files.

    ``request.all()`` does not contain uploads, so without this merge file rules would
    never see a file (``image`` would be skipped and ``required`` would always fail).
    """
    data = request.all()
    data = dict(data) if isinstance(data, dict) else {}
    try:
        content_type = request.header("content-type", "") or ""
        if "multipart/form-data" not in str(content_type).lower():
            return data
        request._form()  # parsing the form is what fills the upload maps
        lists = getattr(request, "_parsed_file_lists", None)
        if isinstance(lists, dict) and lists:
            grouped = {name: list(items) for name, items in lists.items()}
        else:
            single = getattr(request, "_parsed_files", None)
            grouped = {name: [item] for name, item in single.items()} if isinstance(single, dict) else {}
    except Exception:
        return data
    for name, items in grouped.items():
        # A file input left empty is still submitted, as a zero-byte part: that is "no file".
        items = [item for item in items if getattr(item, "size", 1)]
        if not items:
            continue
        # One file per name is the file itself; a repeated name (``multiple``) is a list.
        data[name] = items[0] if len(items) == 1 else items
    return data


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
        """Return only the validated fields declared in rules() (uploaded files included).

        Nested rules return just the validated leaves: a rule on ``address.city`` yields
        ``{"address": {"city": ...}}`` and never the rest of ``address``.
        """
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

        v = Validator.make(request_data(self.request), self.rules())
        if self.messages():
            v.messages(self.messages())

        # Only the leaves addressed by rules() are returned — mass-assignment safety.
        validated = v.validate()

        self.after_validation(v)

        return validated
