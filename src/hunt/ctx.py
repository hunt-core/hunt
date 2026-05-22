from __future__ import annotations

from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("hunt.request_id", default="")
