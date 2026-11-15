from __future__ import annotations

import inspect
import logging
from typing import Any

_log = logging.getLogger(__name__)


def _allow(value: Any) -> bool:
    """Interpret the result of a policy callable, failing closed.

    Only the literal ``True`` grants access. An awaitable (an ``async def``
    policy called from the synchronous admin controllers) is truthy but has
    not produced a decision, so it is rejected; so is any other non-``True``
    value such as a model instance, ``1`` or a mock.
    """
    if value is True:
        return True
    if inspect.isawaitable(value):
        close = getattr(value, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        _log.error(
            "Admin policy returned an awaitable; async policies are not supported on resources "
            "and are treated as a denial. Make the can_* method synchronous."
        )
        return False
    if value:
        _log.warning(
            "Admin policy returned %r instead of True; treated as a denial. Return a real bool, e.g. bool(user.is_admin).",
            type(value).__name__,
        )
    return False


def allowed(resource: Any, ability: str, request: Any, *args: Any) -> bool:
    """Call ``resource.<ability>(request, *args)`` and interpret it with ``_allow``.

    A policy that raises is a denial, never a 500 that might be retried open.
    """
    try:
        result = getattr(resource, ability)(request, *args)
    except Exception:
        _log.exception("Admin policy %s.%s raised; denying access", type(resource).__name__, ability)
        return False
    return _allow(result)
