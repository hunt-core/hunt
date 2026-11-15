from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from contextvars import ContextVar

# Per-request query tracker: maps normalised SQL → count
_query_counts: ContextVar[dict[str, int] | None] = ContextVar("_query_counts", default=None)

# Per-request query log: list of (sql, elapsed_ms) for the debug panel
_query_log: ContextVar[list[tuple[str, float]] | None] = ContextVar("_query_log", default=None)

# Warn when the same query pattern runs more than this many times per request
N1_THRESHOLD = 10


def reset_query_tracker() -> None:
    """Call at the start of each request to clear per-request counters and log."""
    _query_counts.set(defaultdict(int))
    _query_log.set([])


def get_query_log() -> list[tuple[str, float]]:
    """Return the current request's query log as a list of (sql, elapsed_ms) tuples."""
    log = _query_log.get()
    return list(log) if log is not None else []


def _is_debug() -> bool:
    return os.environ.get("APP_DEBUG", "false").lower() == "true"


def _normalise(sql: str) -> str:
    """Strip literal values so repeated queries with different bindings match."""
    return re.sub(r":[a-zA-Z0-9_]+", "?", sql).strip()


# Columns / bind names whose values must never reach a log file.
_SENSITIVE_RE = re.compile(
    r"pass(word|wd|phrase)?|secret|token|api_?key|auth|credential|remember|session|cookie|signature|"
    r"private|salt|hash|otp|totp|two_factor|recovery|ssn|card|cvv|iban",
    re.IGNORECASE,
)
_INSERT_COLUMNS_RE = re.compile(r"^\s*INSERT\s+INTO\s+[\w.]+\s*\(([^)]*)\)", re.IGNORECASE)
_COMPARE_RE = re.compile(
    r"([A-Za-z_][\w.]*)\s*(?:=|!=|<>|<=|>=|<|>|\+|-|\bNOT\s+LIKE\b|\bLIKE\b|\bNOT\s+ILIKE\b|\bILIKE\b)"
    r"\s*(?:LOWER\()?:(\w+)",
    re.IGNORECASE,
)
_IN_RE = re.compile(r"([A-Za-z_][\w.]*)\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"([A-Za-z_][\w.]*)\s+BETWEEN\s+:(\w+)\s+AND\s+:(\w+)", re.IGNORECASE)
_POSITIONAL_BIND_RE = re.compile(r"^_(?:v|r\d+_)(\d+)$")
_MAX_LOGGED_VALUE = 200


def _bind_columns(sql: str, bindings: dict) -> dict[str, str]:
    """Best-effort map of bind name -> the column it is compared with or assigned to."""
    columns: dict[str, str] = {}
    insert = _INSERT_COLUMNS_RE.match(sql)
    if insert:
        insert_cols = [c.strip() for c in insert.group(1).split(",")]
        for key in bindings:
            m = _POSITIONAL_BIND_RE.match(str(key))
            if m and int(m.group(1)) < len(insert_cols):
                columns[str(key)] = insert_cols[int(m.group(1))]
    for col, key in _COMPARE_RE.findall(sql):
        columns.setdefault(key, col)
    for col, binds in _IN_RE.findall(sql):
        for key in re.findall(r":(\w+)", binds):
            columns.setdefault(key, col)
    for col, start, end in _BETWEEN_RE.findall(sql):
        columns.setdefault(start, col)
        columns.setdefault(end, col)
    return columns


def redact_bindings(sql: str, bindings: dict) -> dict:
    """Return a copy of *bindings* that is safe to log.

    A value is replaced by ``[REDACTED]`` when its bind name or the column it
    belongs to looks sensitive (password, token, secret, ...); other values
    are truncated so a large payload cannot flood the log.
    """
    if not isinstance(bindings, dict):
        return {}
    columns = _bind_columns(sql, bindings)
    safe: dict = {}
    for key, value in bindings.items():
        name = str(key)
        if _SENSITIVE_RE.search(name) or _SENSITIVE_RE.search(columns.get(name, "")):
            safe[key] = "[REDACTED]"
        elif isinstance(value, (bytes, bytearray)):
            safe[key] = f"<{len(value)} bytes>"
        elif isinstance(value, str) and len(value) > _MAX_LOGGED_VALUE:
            safe[key] = value[:_MAX_LOGGED_VALUE] + "..."
        else:
            safe[key] = value
    return safe


def log_query(sql: str, bindings: dict, elapsed_ms: float) -> None:
    """Log a query when APP_DEBUG is true and check for N+1 patterns."""
    # Always record in the per-request log (used by the debug panel)
    log = _query_log.get()
    if log is not None:
        log.append((sql, elapsed_ms))

    if not _is_debug():
        return

    try:
        from hunt.log.manager import Log

        Log.debug(
            f"[DB] {sql}",
            bindings=str(redact_bindings(sql, bindings)) if bindings else "",
            ms=f"{elapsed_ms:.2f}",
        )
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
