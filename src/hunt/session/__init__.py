import os

from hunt.session.redis_store import RedisSessionStore
from hunt.session.registry import SessionRegistry, revoke_sessions_for
from hunt.session.store import FileSessionStore

__all__ = ["FileSessionStore", "RedisSessionStore", "SessionRegistry", "revoke_sessions_for", "session_driver"]


def session_driver() -> str:
    """Resolve the session driver: config/session.py first, env var fallback."""
    try:
        from hunt.support.helpers import config as _cfg

        driver = _cfg("session.driver", None)
        if driver:
            return str(driver).lower()
    except Exception:
        pass
    return os.environ.get("SESSION_DRIVER", "file").lower()
