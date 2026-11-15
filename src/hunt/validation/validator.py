from __future__ import annotations

import inspect
from typing import Any

from hunt.validation.rules import IMPLICIT_RULES, PARAM_CHECKS, RAW_PARAMETER_RULES, RULES, RuleContext, lookup

_META_RULES = ("bail", "nullable", "sometimes")


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

    return _expand_wildcard(field, data)[0]


def _expand_wildcard(field: str, data: Any) -> tuple[list[str], list[tuple[str, str]]]:
    """Expand wildcards and report parents that could not be expanded.

    Returns ``(paths, problems)`` where each problem is ``(parent_path, kind)`` and kind is
    ``"missing"`` (no such key, or None) or ``"not_list"`` (present but not a list).
    """
    if ".*" not in field:
        return [field], []

    star_pos = field.index(".*")
    prefix = field[:star_pos]
    suffix = field[star_pos + 2 :].lstrip(".")  # strip leading dot after .*

    lst = _get_nested(data, prefix) if prefix else data
    if lst is None:
        return [], [(prefix, "missing")]
    if not isinstance(lst, (list, tuple)):
        return [], [(prefix, "not_list")]

    paths: list[str] = []
    problems: list[tuple[str, str]] = []
    for i in range(len(lst)):
        concrete = f"{prefix}.{i}.{suffix}" if suffix else f"{prefix}.{i}"
        sub_paths, sub_problems = _expand_wildcard(concrete, data)
        paths.extend(sub_paths)
        problems.extend(sub_problems)
    return paths, problems


def _split_rules(definition: str) -> list[str]:
    """Split ``"a|b:1|regex:^(x|y)$"`` on ``|``.

    A pattern may itself contain ``|``, so once a ``regex:`` / ``not_regex:`` rule starts,
    the rest of the string belongs to it. Put such a rule last, or use the list form
    (``["required", "regex:^(a|b)$", "max:5"]``) where nothing is split.
    """
    rules: list[str] = []
    rest = definition
    while rest:
        name = rest.split(":", 1)[0].split("|", 1)[0]
        if name in ("regex", "not_regex") and rest.startswith(name + ":"):
            rules.append(rest)
            break
        part, sep, rest = rest.partition("|")
        if part:
            rules.append(part)
        if not sep:
            break
    return rules


def _parse_rule(raw_rule: str) -> tuple[str, list[str]]:
    name, sep, arg = raw_rule.partition(":")
    if not sep:
        return name, []
    if name in RAW_PARAMETER_RULES:
        return name, [arg]
    return name, arg.split(",") if arg != "" else []


_context_cache: dict[Any, bool] = {}


def _accepts_context(fn: Any) -> bool:
    """True when a rule function takes a fourth positional argument (the RuleContext)."""
    try:
        return _context_cache[fn]
    except (KeyError, TypeError):
        pass
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    result = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params) or (
        len([p for p in params if p.kind in kinds]) >= 4
    )
    try:
        if len(_context_cache) < 1024:
            _context_cache[fn] = result
    except TypeError:
        pass
    return result


def _copy_path(source: Any, target: Any, parts: list[str]) -> Any:
    """Copy the leaf at ``parts`` from ``source`` into ``target``, creating containers of
    the same shape as the source along the way. Returns the (possibly new) target."""
    if not parts:
        return source
    head, rest = parts[0], parts[1:]
    if isinstance(source, dict):
        if head not in source:
            return target
        if not isinstance(target, dict):
            target = {}
        existing = target.get(head)
        target[head] = _copy_path(source[head], existing, rest) if rest else source[head]
        return target
    if isinstance(source, (list, tuple)) and head.isdigit() and head.isascii():
        idx = int(head)
        if idx >= len(source):
            return target
        if not isinstance(target, list):
            target = []
        while len(target) <= idx:
            target.append(None)
        target[idx] = _copy_path(source[idx], target[idx], rest) if rest else source[idx]
        return target
    return target


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
        self._data = data if isinstance(data, dict) else {}
        self._rules = rules
        self._errors: dict[str, list[str]] = {}
        self._custom_messages: dict[str, str] = {}
        self._validated: dict[str, Any] = {}
        # Parse every rule now: an unknown rule name or a broken pattern is a programming
        # error and must surface when the validator is built, not silently validate nothing.
        self._parsed: dict[str, list[Any]] = {field: self._parse_definition(field, d) for field, d in rules.items()}

    @classmethod
    def make(cls, data: dict, rules: dict) -> Validator:
        return cls(data, rules)

    @staticmethod
    def _parse_definition(field: str, definition: Any) -> list[Any]:
        """Turn one field's rules into ``[(name, params) | rule_object, ...]``."""
        if definition is None:
            return []
        if isinstance(definition, str):
            raw_rules: list[Any] = _split_rules(definition)
        elif isinstance(definition, (list, tuple)):
            raw_rules = list(definition)
        else:
            raw_rules = [definition]

        parsed: list[Any] = []
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, str):
                parsed.append(raw_rule)  # class-based rule object
                continue
            if raw_rule == "":
                continue
            name, params = _parse_rule(raw_rule)
            if name not in _META_RULES:
                if name not in RULES:
                    raise ValueError(f"Unknown validation rule {name!r} for field {field!r}")
                check = PARAM_CHECKS.get(name)
                if check is not None:
                    try:
                        check(params)
                    except ValueError as exc:
                        raise ValueError(f"Invalid validation rule {raw_rule!r} for field {field!r}: {exc}") from exc
            parsed.append((name, params))
        return parsed

    def messages(self, custom: dict[str, str]) -> Validator:
        self._custom_messages = custom
        return self

    def validate(self) -> dict:
        """Run the rules and return only the validated data.

        The result contains just the keys that have rules (nested paths and expanded
        wildcards included), never the rest of the input, so it is safe to pass on to
        ``Model.create()``.
        """
        self._run()
        if self._errors:
            raise ValidationException(self._errors)
        return self.validated()

    def validated(self) -> dict:
        """The validated subset of the input from the last run (see :meth:`validate`)."""
        return self._validated

    def passes(self) -> bool:
        self._run()
        return not self._errors

    def fails(self) -> bool:
        return not self.passes()

    def errors(self) -> MessageBag:
        return MessageBag(self._errors)

    def _add_error(self, field: str, rule_name: str, msg: str, pattern: str | None = None) -> None:
        final_msg = self._custom_messages.get(f"{field}.{rule_name}")
        if final_msg is None and pattern is not None:
            final_msg = self._custom_messages.get(f"{pattern}.{rule_name}")
        if final_msg is None:
            final_msg = msg
        final_msg = final_msg.replace("The field", f"The {field} field")
        self._errors.setdefault(field, []).append(final_msg)

    def _run(self) -> None:
        self._errors = {}
        self._validated = {}

        # Expand wildcard patterns (e.g. "items.*.name") into concrete paths
        expanded_rules: dict[str, tuple[str, list[Any]]] = {}
        for field, parsed in self._parsed.items():
            if ".*" not in field:
                expanded_rules[field] = (field, parsed)
                continue
            names = [r[0] for r in parsed if isinstance(r, tuple)]
            paths, problems = _expand_wildcard(field, self._data)
            for concrete in paths:
                expanded_rules[concrete] = (field, parsed)
            for parent, kind in problems:
                if "sometimes" in names and not lookup(self._data, parent)[0]:
                    continue
                if kind == "not_list":
                    if not self._errors.get(parent):
                        self._add_error(parent, "array", "The field must be an array.")
                elif "required" in names and not self._errors.get(parent):
                    # No parent means no elements, which would silently satisfy "required".
                    self._add_error(parent, "required", "The field is required.")

        for field, (pattern, parsed) in expanded_rules.items():
            value = lookup(self._data, field)[1]

            # --- Meta-rule flags ---
            rule_names = [(r[0] if isinstance(r, tuple) else getattr(r, "__name__", "")) for r in parsed]
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

            ctx = RuleContext(field, self._data, rule_names)

            for rule in parsed:
                # Handle class-based custom rules
                if not isinstance(rule, tuple):
                    rule_obj = rule
                    if hasattr(rule_obj, "passes") and not rule_obj.passes(field, value):
                        msg = rule_obj.message() if hasattr(rule_obj, "message") else "The field is invalid."
                        msg = msg.replace(":attribute", field)
                        self._errors.setdefault(field, []).append(msg)
                        if bail:
                            break
                    continue

                rule_name, params = rule

                # Meta-rules that are handled above — skip in loop
                if rule_name in _META_RULES:
                    continue

                # Inject field name for `confirmed`
                if rule_name == "confirmed" and not params:
                    params = [field]

                # Skip non-required rules when value is absent (unless it's a required_* rule)
                if value is None and not rule_name.startswith("required") and rule_name not in IMPLICIT_RULES:
                    if not has_required:
                        continue

                fn = RULES.get(rule_name)
                if fn is None:
                    # Removed from the registry after this validator was built.
                    raise ValueError(f"Unknown validation rule {rule_name!r} for field {field!r}")

                msg = fn(value, params, self._data, ctx) if _accepts_context(fn) else fn(value, params, self._data)
                if msg:
                    self._add_error(field, rule_name, msg, pattern)
                    if bail:
                        break

        if not self._errors:
            self._validated = self._collect_validated(list(expanded_rules))

    def _collect_validated(self, paths: list[str]) -> dict[str, Any]:
        """Build the output from the rule paths only.

        A path with rules on deeper paths (``address`` next to ``address.city``) is a
        container: only its validated children are copied, never the whole value, unless
        the value holds nothing that could carry extra keys (None, a scalar, or empty).
        """
        patterns = list(self._parsed)
        output: dict[str, Any] = {}
        for path in paths:
            present, value = lookup(self._data, path)
            if not present:
                continue
            is_container = any(other.startswith(path + ".") for other in paths) or any(
                self._pattern_is_below(path, other) for other in patterns
            )
            if is_container and isinstance(value, (dict, list, tuple)):
                if len(value) > 0 or isinstance(lookup(output, path)[1], (dict, list)):
                    continue
            output = _copy_path(self._data, output, path.split("."))
        return output

    @staticmethod
    def _pattern_is_below(path: str, pattern: str) -> bool:
        """True when rule ``pattern`` (possibly with wildcards) addresses something inside ``path``."""
        path_parts = path.split(".")
        pattern_parts = pattern.split(".")
        if len(pattern_parts) <= len(path_parts):
            return False
        return all(pp == "*" or pp == part for pp, part in zip(pattern_parts, path_parts, strict=False))
