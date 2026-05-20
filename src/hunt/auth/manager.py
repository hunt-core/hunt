from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.session.store import FileSessionStore

# Per-coroutine request context — safe under async concurrency
_request_var: ContextVar[Any] = ContextVar("_request_var", default=None)
# Per-request "once" user — set by Auth.once() / Auth.once_using_id(); not persisted to session
_once_user_var: ContextVar[Any] = ContextVar("_once_user_var", default=None)


def _set_request(request: Any) -> None:
    _request_var.set(request)


def _get_current_request() -> Any:
    return _request_var.get()


def _get_session() -> FileSessionStore | None:
    req = _request_var.get()
    if req is None:
        return None
    return getattr(req, "_session", None)


# ---------------------------------------------------------------------------
# Guard drivers
# ---------------------------------------------------------------------------


class _SessionGuard:
    """Auth guard backed by the request session (cookie-based)."""

    def __init__(self, name: str, model: type | None = None, username: str = "email") -> None:
        self._name = name
        self._model = model
        self._username = username
        self._session_key = "_auth_id" if name == "web" else f"_auth_id_{name}"

    def set_model(self, model: type) -> None:
        self._model = model

    def user(self) -> Any | None:
        once_user = _once_user_var.get()
        if once_user is not None:
            return once_user
        session = _get_session()
        if session is None:
            return None
        user_id = session.get(self._session_key)
        if user_id is None:
            return None
        if self._model is None:
            return None
        try:
            return self._model.find(user_id)
        except Exception:
            return None

    def id(self) -> Any | None:
        once_user = _once_user_var.get()
        if once_user is not None:
            return once_user._attributes.get("id")
        session = _get_session()
        return session.get(self._session_key) if session else None

    def check(self) -> bool:
        return self.user() is not None

    def guest(self) -> bool:
        return not self.check()

    def attempt(self, credentials: dict[str, Any]) -> bool:
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        password = credentials.get("password", "")
        identifier = credentials.get(self._username)
        user = self._model.where(self._username, identifier).first()
        if user is None:
            return False
        hashed = user._attributes.get("password", "")
        if not verify_password(password, hashed):
            return False
        if user._attributes.get("two_factor_enabled"):
            session = _get_session()
            if session is None:
                raise RuntimeError("Session middleware is not active.")
            session.regenerate()
            session.put("_2fa_pending", user._attributes["id"])
            return False
        self.login(user)
        return True

    def two_factor_pending(self) -> bool:
        """Return True if a 2FA challenge is awaiting completion."""
        session = _get_session()
        return session.get("_2fa_pending") is not None if session else False

    def login(self, user: Any) -> None:
        session = _get_session()
        if session is None:
            raise RuntimeError("Session middleware is not active.")
        session.regenerate()
        session.put(self._session_key, user._attributes["id"])

    def login_using_id(self, user_id: Any) -> Any | None:
        """Fetch a user by primary key and log them in. Returns the user or None."""
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        user = self._model.find(user_id)
        if user is None:
            return None
        self.login(user)
        return user

    def once(self, credentials: dict[str, Any]) -> bool:
        """Authenticate for the current request only — no session is written."""
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        password = credentials.get("password", "")
        identifier = credentials.get(self._username)
        user = self._model.where(self._username, identifier).first()
        if user is None:
            return False
        if not verify_password(password, user._attributes.get("password", "")):
            return False
        _once_user_var.set(user)
        return True

    def once_using_id(self, user_id: Any) -> Any | None:
        """Authenticate a specific user for the current request only."""
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        user = self._model.find(user_id)
        if user is None:
            return None
        _once_user_var.set(user)
        return user

    def logout(self) -> None:
        session = _get_session()
        if session:
            session.forget(self._session_key)
            session.regenerate()


class _TokenGuard:
    """Auth guard backed by a static Bearer token or query-string token."""

    def __init__(
        self,
        name: str,
        model: type | None = None,
        field: str = "api_token",
    ) -> None:
        self._name = name
        self._model = model
        self._field = field
        self._cached_user: Any = None

    def set_model(self, model: type) -> None:
        self._model = model

    def _extract_token(self) -> str | None:
        req = _get_current_request()
        if req is None:
            return None
        token = req.bearer_token()
        if token:
            return token
        return req.query(self._field)

    def user(self) -> Any | None:
        token = self._extract_token()
        if not token or self._model is None:
            return None
        import hashlib

        hashed = hashlib.sha256(token.encode()).hexdigest()
        return self._model.where(self._field, hashed).first()

    def id(self) -> Any | None:
        u = self.user()
        return u._attributes.get("id") if u else None

    def check(self) -> bool:
        return self.user() is not None

    def guest(self) -> bool:
        return not self.check()

    def attempt(self, credentials: dict[str, Any]) -> bool:
        raise NotImplementedError("Token guards authenticate via static tokens, not credentials.")

    def login(self, user: Any) -> None:
        raise NotImplementedError("Token guards do not maintain session login state.")

    def logout(self) -> None:
        raise NotImplementedError("Token guards do not maintain session login state.")


# ---------------------------------------------------------------------------
# Auth manager
# ---------------------------------------------------------------------------


class _AuthManager:
    """Multi-guard authentication manager.

    Usage::

        # Default web guard (backward-compatible)
        Auth.user()
        Auth.attempt({"email": ..., "password": ...})

        # Named guard
        Auth.guard("api").user()

        # Configure guards (call from AppServiceProvider.boot)
        Auth.configure({
            "web":  {"driver": "session", "model": User},
            "api":  {"driver": "token",   "model": User, "field": "api_token"},
        }, default="web")
    """

    def __init__(self) -> None:
        self._guards: dict[str, Any] = {}
        self._default_name: str = "web"
        # Built-in default guard (used before configure() is called)
        self._default_guard = _SessionGuard("web")

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, guards: dict[str, dict], default: str = "web") -> None:
        self._default_name = default
        for name, config in guards.items():
            driver = config.get("driver", "session")
            model = config.get("model")
            if driver == "session":
                username = config.get("username", "email")
                g: Any = _SessionGuard(name, model, username)
            elif driver == "token":
                field = config.get("field", "api_token")
                g = _TokenGuard(name, model, field)
            else:
                raise ValueError(f"Unknown guard driver: {driver!r}")
            self._guards[name] = g
        # Keep default guard in sync if "web" was configured
        if "web" in self._guards:
            self._default_guard = self._guards["web"]

    def guard(self, name: str) -> Any:
        """Return the named guard instance."""
        if name in self._guards:
            return self._guards[name]
        # Lazily create a session guard with the same name
        g = _SessionGuard(name, self._default_guard._model)
        self._guards[name] = g
        return g

    # ------------------------------------------------------------------
    # Legacy / default-guard proxy (backward-compatible)
    # ------------------------------------------------------------------

    def set_model(self, model: type) -> None:
        self._default_guard.set_model(model)

    def user(self) -> Any | None:
        return self._default_guard.user()

    def id(self) -> Any | None:
        return self._default_guard.id()

    def check(self) -> bool:
        return self._default_guard.check()

    def guest(self) -> bool:
        return self._default_guard.guest()

    def attempt(self, credentials: dict[str, Any]) -> bool:
        return self._default_guard.attempt(credentials)

    def login(self, user: Any) -> None:
        self._default_guard.login(user)

    def login_using_id(self, user_id: Any) -> Any | None:
        return self._default_guard.login_using_id(user_id)

    def once(self, credentials: dict[str, Any]) -> bool:
        return self._default_guard.once(credentials)

    def once_using_id(self, user_id: Any) -> Any | None:
        return self._default_guard.once_using_id(user_id)

    def logout(self) -> None:
        self._default_guard.logout()


Auth = _AuthManager()


# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt

    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False
