from __future__ import annotations

from typing import Any

from hunt.validation.rules import RULES


class ValidationException(Exception):
    def __init__(self, errors: dict[str, list[str]]) -> None:
        self.errors = errors
        super().__init__(str(errors))

    def first(self, field: str) -> str | None:
        msgs = self.errors.get(field, [])
        return msgs[0] if msgs else None

    def all(self) -> list[str]:
        return [msg for msgs in self.errors.values() for msg in msgs]


class MessageBag:
    def __init__(self, errors: dict[str, list[str]]) -> None:
        self._errors = errors

    def first(self, field: str) -> str | None:
        msgs = self._errors.get(field, [])
        return msgs[0] if msgs else None

    def get(self, field: str) -> list[str]:
        return self._errors.get(field, [])

    def all(self) -> list[str]:
        return [msg for msgs in self._errors.values() for msg in msgs]

    def has(self, field: str) -> bool:
        return field in self._errors and bool(self._errors[field])

    def any(self) -> bool:
        return bool(self._errors)

    def __contains__(self, field: str) -> bool:
        return self.has(field)


class Validator:
    def __init__(self, data: dict[str, Any], rules: dict[str, str | list]) -> None:
        self._data = data
        self._rules = rules
        self._errors: dict[str, list[str]] = {}
        self._custom_messages: dict[str, str] = {}

    @classmethod
    def make(cls, data: dict, rules: dict) -> "Validator":
        return cls(data, rules)

    def messages(self, custom: dict[str, str]) -> "Validator":
        self._custom_messages = custom
        return self

    def validate(self) -> dict:
        self._run()
        if self._errors:
            raise ValidationException(self._errors)
        return self._data

    def passes(self) -> bool:
        self._run()
        return not self._errors

    def fails(self) -> bool:
        return not self.passes()

    def errors(self) -> MessageBag:
        return MessageBag(self._errors)

    def _run(self) -> None:
        self._errors = {}
        for field, rule_def in self._rules.items():
            raw_rules = rule_def if isinstance(rule_def, list) else rule_def.split("|")
            value = self._data.get(field)
            is_required = any(
                (r if isinstance(r, str) else r).startswith("required") for r in raw_rules
            )
            for raw_rule in raw_rules:
                if isinstance(raw_rule, str):
                    parts = raw_rule.split(":")
                    rule_name = parts[0]
                    params = parts[1].split(",") if len(parts) > 1 else []
                else:
                    rule_name = raw_rule
                    params = []

                # Inject field name for the confirmed rule so it can find {field}_confirmation
                if rule_name == "confirmed" and not params:
                    params = [field]

                if value is None and rule_name != "required":
                    if not is_required:
                        continue

                fn = RULES.get(str(rule_name))
                if fn is None:
                    continue

                msg = fn(value, params, self._data)
                if msg:
                    custom_key = f"{field}.{rule_name}"
                    final_msg = self._custom_messages.get(custom_key, msg)
                    final_msg = final_msg.replace("The field", f"The {field} field")
                    self._errors.setdefault(field, []).append(final_msg)
