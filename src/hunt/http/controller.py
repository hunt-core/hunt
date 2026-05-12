from __future__ import annotations

from typing import Any, TYPE_CHECKING

from hunt.http.response import JsonResponse, Response, response, json_response, redirect

if TYPE_CHECKING:
    from hunt.http.request import Request


class Controller:
    def response(self, content: str = "", status: int = 200) -> Response:
        return response(content, status)

    def json(self, data: Any, status: int = 200) -> JsonResponse:
        return json_response(data, status)

    def redirect(self, url: str, status: int = 302) -> Response:
        return redirect(url, status)

    def view(self, template: str, data: dict | None = None) -> Response:
        from hunt.support.helpers import view as make_view
        rendered = make_view(template, data or {})
        return response(str(rendered))

    def validate(self, request: "Request", rules: dict) -> dict:
        from hunt.validation.validator import Validator
        v = Validator.make(request.all(), rules)
        return v.validate()
