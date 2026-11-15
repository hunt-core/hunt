"""Standard 5-field cron expressions (minute hour day-of-month month day-of-week).

Semantics follow Vixie cron:

* day-of-week is ``0-7`` where both ``0`` and ``7`` mean Sunday; ``SUN``-``SAT``
  and ``JAN``-``DEC`` names are accepted (case-insensitive);
* each field is a comma list of ``*``, ``n``, ``a-b``, and any of those with a
  ``/step`` (``*/5``, ``10-40/10``, ``5/15`` meaning "from 5 to the maximum");
* when both day-of-month and day-of-week are restricted (neither starts with
  ``*``), a date matches if *either* field matches.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1
    )
}
_DAY_NAMES = {name: number for number, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))}

# (label, lowest, highest, names)
_FIELDS: tuple[tuple[str, int, int, dict[str, int] | None], ...] = (
    ("minute", 0, 59, None),
    ("hour", 0, 23, None),
    ("day-of-month", 1, 31, None),
    ("month", 1, 12, _MONTH_NAMES),
    ("day-of-week", 0, 7, _DAY_NAMES),
)


def _number(token: str, lo: int, hi: int, names: dict[str, int] | None, label: str) -> int:
    text = token.strip()
    if names is not None and text.upper() in names:
        return names[text.upper()]
    if not text.isdigit():
        raise ValueError(f"Invalid cron {label} value {token!r}")
    value = int(text)
    if not lo <= value <= hi:
        raise ValueError(f"Cron {label} value {value} is out of range {lo}-{hi}")
    return value


def _expand_part(part: str, lo: int, hi: int, names: dict[str, int] | None = None, label: str = "field") -> set[int]:
    """Return the set of values one comma-separated part stands for."""
    part = part.strip()
    if not part:
        raise ValueError(f"Empty cron {label} entry")
    base, slash, step_text = part.partition("/")
    step = 1
    if slash:
        if not step_text.strip().isdigit() or int(step_text) <= 0:
            raise ValueError(f"Invalid cron {label} step {step_text!r} in {part!r}: must be a positive integer")
        step = int(step_text)

    if base == "*":
        start, end = lo, hi
    elif "-" in base:
        start_text, _, end_text = base.partition("-")
        start = _number(start_text, lo, hi, names, label)
        end = _number(end_text, lo, hi, names, label)
        if end < start and names is _DAY_NAMES and end == 0:
            end = 7  # e.g. FRI-SUN
        if end < start:
            raise ValueError(f"Cron {label} range {base!r} runs backwards")
    else:
        start = _number(base, lo, hi, names, label)
        # "n/step" means "from n to the maximum, every step".
        end = hi if slash else start
    return set(range(start, end + 1, step))


def _expand_field(field: str, lo: int, hi: int, names: dict[str, int] | None = None, label: str = "field") -> set[int]:
    values: set[int] = set()
    for part in field.split(","):
        values |= _expand_part(part, lo, hi, names, label)
    return values


@lru_cache(maxsize=512)
def _parse(
    expression: str,
) -> tuple[frozenset[int], frozenset[int], frozenset[int], frozenset[int], frozenset[int], bool]:
    if not isinstance(expression, str):
        raise ValueError(f"Invalid cron expression: {expression!r}")
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {expression!r}")
    try:
        parsed = [
            frozenset(_expand_field(field, lo, hi, names, label))
            for field, (label, lo, hi, names) in zip(fields, _FIELDS, strict=True)
        ]
    except ValueError as exc:
        raise ValueError(f"Invalid cron expression {expression!r}: {exc}") from None
    # 7 is an alias for Sunday (0).
    parsed[4] = frozenset(0 if value == 7 else value for value in parsed[4])
    # Vixie rule: the two day fields are ORed only when both are restricted.
    either_day = not fields[2].startswith("*") and not fields[4].startswith("*")
    return parsed[0], parsed[1], parsed[2], parsed[3], parsed[4], either_day


def validate(expression: str) -> str:
    """Return *expression* if it is a valid cron expression, else raise ``ValueError``."""
    _parse(expression)
    return expression


def matches(expression: str, dt: datetime | None = None) -> bool:
    """Return True if *dt* (default: now) matches the 5-field cron expression.

    Raises ``ValueError`` for an invalid expression.
    """
    if dt is None:
        dt = datetime.now()

    minutes, hours, days, months, weekdays, either_day = _parse(expression)
    if dt.minute not in minutes or dt.hour not in hours or dt.month not in months:
        return False
    day_ok = dt.day in days
    # datetime.weekday() is 0=Mon … 6=Sun; cron is 0=Sun … 6=Sat.
    weekday_ok = (dt.weekday() + 1) % 7 in weekdays
    return (day_ok or weekday_ok) if either_day else (day_ok and weekday_ok)


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    return value in _expand_field(field, lo, hi)


def _part_matches(part: str, value: int, lo: int, hi: int) -> bool:
    return value in _expand_part(part, lo, hi)
