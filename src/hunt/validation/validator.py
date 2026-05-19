from __future__ import annotations

from typing import Any

from hunt.validation.rules import RULES


def _get_nested(data: Any, path: str) -> Any:
    """Walk nested dicts/lists by a dot-separated path (e.g. 'address.city', 'items.0.name')."""
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
    return current


def _expand_wildcard_field(field: str, data: dict) -> list[str]:
    """Expand 'a.*.b' wildcards into concrete paths based on the current data.

    Supports multiple wildcards via recursion: 'a.*.b.*.c' works.
    Returns an empty list when the list is absent or empty.
    """
    if ".*" not in field:
        return [field]

    star_pos = field.index(".*")
    prefix = field[:star_pos]
    suffix = field[star_pos + 2 :].lstrip(".")  # strip leading dot after .*

    lst = _get_nested(data, prefix) if prefix else data
    if not isinstance(lst, (list, tuple)):
        return []

    result = []
    for i in range(len(lst)):
        concrete = f"{prefix}.{i}.{suffix}" if suffix else f"{prefix}.{i}"
        if ".*" in concrete:
            result.extend(_expand_wildcard_field(concrete, data))
        else:
            result.append(concrete)
    return result


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
    def make(cls, data: dict, rules: dict) -> Validator:
        return cls(data, rules)

    def messages(self, custom: dict[str, str]) -> Validator:
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

        # Expand wildcard patterns (e.g. "items.*.name") into concrete paths
        expanded_rules: dict[str, Any] = {}
        for field, rule_def in self._rules.items():
            if ".*" in field:
                for concrete in _expand_wildcard_field(field, self._data):
                    expanded_rules[concrete] = rule_def
            else:
                expanded_rules[field] = rule_def

        for field, rule_def in expanded_rules.items():
            raw_rules = rule_def if isinstance(rule_def, list) else rule_def.split("|")
            value = _get_nested(self._data, field) if "." in field else self._data.get(field)

            # --- Meta-rule flags ---
            rule_names = [(r.split(":")[0] if isinstance(r, str) else getattr(r, "__name__", "")) for r in raw_rules]
            bail = "bail" in rule_names
            nullable = "nullable" in rule_names
            sometimes = "sometimes" in rule_names
            has_required = any(n.startswith("required") for n in rule_names)

            # `sometimes`: skip entirely if field not present in input
            top_key = field.split(".")[0]
            if sometimes and top_key not in self._data:
                continue

            # `nullable`: if value is None/empty and not required, skip all rules
            if nullable and (value is None or value == "") and not has_required:
                continue

            for raw_rule in raw_rules:
                # Handle class-based custom rules
                if not isinstance(raw_rule, str):
                    rule_obj = raw_rule
                    if hasattr(rule_obj, "passes") and not rule_obj.passes(field, value):
                        msg = rule_obj.message() if hasattr(rule_obj, "message") else "The field is invalid."
                        msg = msg.replace(":attribute", field)
                        self._errors.setdefault(field, []).append(msg)
                        if bail:
                            break
                    continue

                parts = raw_rule.split(":")
                rule_name = parts[0]
                params = parts[1].split(",") if len(parts) > 1 else []

                # Meta-rules that are handled above — skip in loop
                if rule_name in ("bail", "nullable", "sometimes"):
                    continue

                # Inject field name for `confirmed`
                if rule_name == "confirmed" and not params:
                    params = [field]

                # Skip non-required rules when value is absent (unless it's a required_* rule)
                if value is None and not rule_name.startswith("required"):
                    if not has_required:
                        continue

                fn = RULES.get(rule_name)
                if fn is None:
                    continue

                msg = fn(value, params, self._data)
                if msg:
                    custom_key = f"{field}.{rule_name}"
                    final_msg = self._custom_messages.get(custom_key, msg)
                    final_msg = final_msg.replace("The field", f"The {field} field")
                    self._errors.setdefault(field, []).append(final_msg)
                    if bail:
                        break
