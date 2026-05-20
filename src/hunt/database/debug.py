from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from contextvars import ContextVar

# Per-request query tracker: maps normalised SQL → count
_query_counts: ContextVar[dict[str, int] | None] = ContextVar("_query_counts", default=None)

# Warn when the same query pattern runs more than this many times per request
N1_THRESHOLD = 10


def reset_query_tracker() -> None:
    """Call at the start of each request to clear per-request counters."""
    _query_counts.set(defaultdict(int))


def _is_debug() -> bool:
    return os.environ.get("APP_DEBUG", "false").lower() == "true"


def _normalise(sql: str) -> str:
    """Strip literal values so repeated queries with different bindings match."""
    return re.sub(r":[a-zA-Z0-9_]+", "?", sql).strip()


def log_query(sql: str, bindings: dict, elapsed_ms: float) -> None:
    """Log a query when APP_DEBUG is true and check for N+1 patterns."""
    if not _is_debug():
        return

    try:
        from hunt.log.manager import Log

        Log.debug(f"[DB] {sql}", bindings=str(bindings) if bindings else "", ms=f"{elapsed_ms:.2f}")
    except Exception:
        pass

    counts = _query_counts.get()
    if counts is None:
        return

    key = _normalise(sql)
    counts[key] += 1
    if counts[key] == N1_THRESHOLD:
        try:
            from hunt.log.manager import Log

            Log.warning(f"[N+1] Query executed {N1_THRESHOLD}+ times in one request — possible N+1: {sql[:120]}")
        except Exception:
            pass


def timed_execute(conn, sql_text, bindings: dict):
    """Execute *sql_text* on *conn*, log timing, and return the result."""
    t0 = time.monotonic()
    result = conn.execute(sql_text, bindings)
    elapsed_ms = (time.monotonic() - t0) * 1000
    log_query(sql_text.text if hasattr(sql_text, "text") else str(sql_text), bindings, elapsed_ms)
    return result
