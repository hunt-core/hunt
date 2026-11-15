"""Best-effort logging for background machinery (queue, scheduler, events).

These code paths must never raise because logging is unavailable, so the
framework logger is tried first and the standard library is the fallback.
"""

from __future__ import annotations

import logging

_fallback = logging.getLogger("hunt")


def log_error(message: str) -> None:
    try:
        from hunt.log.manager import Log

        Log.error(message)
    except Exception:
        _fallback.error(message)


def log_warning(message: str) -> None:
    try:
        from hunt.log.manager import Log

        Log.warning(message)
    except Exception:
        _fallback.warning(message)
