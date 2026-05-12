from __future__ import annotations

from contextvars import ContextVar
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from hunt.session.store import FileSessionStore

# Per-coroutine request context — safe under async concurrency
_request_var: ContextVar[Any] = ContextVar("_request_var", default=None)


def _set_request(request: Any) -> None:
    _request_var.set(request)


def _get_current_request() -> Any:
    return _request_var.get()


def _get_session() -> "FileSessionStore | None":
    req = _request_var.get()
    if req is None:
        return None
    return getattr(req, "_session", None)


class _AuthManager:
    """Thread-local-style auth manager backed by the active request session."""

    _user_model: type | None = None

    def set_model(self, model: type) -> None:
        self._user_model = model

    def user(self) -> Any | None:
        session = _get_session()
        if session is None:
            return None
        user_id = session.get("_auth_id")
        if user_id is None:
            return None
        if self._user_model is None:
            return None
        try:
            return self._user_model.find(user_id)
        except Exception:
            return None

    def id(self) -> Any | None:
        session = _get_session()
        return session.get("_auth_id") if session else None

    def check(self) -> bool:
        return self.id() is not None

    def guest(self) -> bool:
        return not self.check()

    def attempt(self, credentials: dict[str, Any]) -> bool:
        """Verify credentials and log in on success."""
        if self._user_model is None:
            raise RuntimeError("Auth model not configured. Call Auth.set_model(UserModel).")

        email_field = "email"
        password = credentials.get("password", "")
        identifier = credentials.get(email_field)

        user = self._user_model.where(email_field, identifier).first()
        if user is None:
            return False

        hashed = user._attributes.get("password", "")
        if not verify_password(password, hashed):
            return False

        self.login(user)
        return True

    def login(self, user: Any) -> None:
        session = _get_session()
        if session is None:
            raise RuntimeError("Session middleware is not active.")
        session.regenerate()
        session.put("_auth_id", user._attributes["id"])

    def logout(self) -> None:
        session = _get_session()
        if session:
            session.forget("_auth_id")
            session.regenerate()


Auth = _AuthManager()


def hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False
