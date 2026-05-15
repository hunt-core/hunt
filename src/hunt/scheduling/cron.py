from __future__ import annotations

from datetime import datetime


def matches(expression: str, dt: datetime | None = None) -> bool:
    """Return True if *dt* (default: now) matches the 5-field cron expression."""
    if dt is None:
        dt = datetime.now()

    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron expression (need 5 fields): {expression!r}")

    minute, hour, dom, month, dow = fields

    return (
        _field_matches(minute, dt.minute, 0, 59)
        and _field_matches(hour, dt.hour, 0, 23)
        and _field_matches(dom, dt.day, 1, 31)
        and _field_matches(month, dt.month, 1, 12)
        and _field_matches(dow, dt.weekday(), 0, 6)  # 0=Mon … 6=Sun
    )


def _field_matches(field: str, value: int, lo: int, hi: int) -> bool:
    for part in field.split(","):
        if _part_matches(part.strip(), value, lo, hi):
            return True
    return False


def _part_matches(part: str, value: int, lo: int, hi: int) -> bool:
    # */n
    if part.startswith("*/"):
        step = int(part[2:])
        return (value - lo) % step == 0

    # *
    if part == "*":
        return True

    # n-m/s or n-m
    if "-" in part:
        range_part, _, step_str = part.partition("/")
        start, _, end = range_part.partition("-")
        start, end = int(start), int(end)
        step = int(step_str) if step_str else 1
        return any(v == value for v in range(start, end + 1, step))

    # plain integer
    return int(part) == value
