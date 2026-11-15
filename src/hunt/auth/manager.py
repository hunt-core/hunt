from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import time
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hunt.session.store import FileSessionStore

_log = logging.getLogger("hunt.auth")

# Per-coroutine request context — safe under async concurrency
_request_var: ContextVar[Any] = ContextVar("_request_var", default=None)
# Per-request "once" users, keyed by guard name — set by Auth.once() / Auth.once_using_id();
# never persisted to the session and reset whenever a new request is bound.
_once_user_var: ContextVar[Any] = ContextVar("_once_user_var", default=None)

_COLUMN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _set_request(request: Any) -> None:
    _request_var.set(request)
    # A reused context (test client, queue worker, keep-alive task) must not carry the
    # previous request's once-user into this one.
    _once_user_var.set(None)


def _once_user(guard: str) -> Any:
    users = _once_user_var.get()
    return users.get(guard) if isinstance(users, dict) else None


def _set_once_user(guard: str, user: Any) -> None:
    users = _once_user_var.get()
    # Always store a new dict: the old one may be shared with a parent context.
    _once_user_var.set({**(users if isinstance(users, dict) else {}), guard: user})


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
        once_user = _once_user(self._name)
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
        once_user = _once_user(self._name)
        if once_user is not None:
            return once_user._attributes.get("id")
        session = _get_session()
        return session.get(self._session_key) if session else None

    def _retrieve_by_credentials(self, credentials: dict[str, Any]) -> tuple[Any | None, bool]:
        """Look the user up and check the password. Returns ``(user, password_ok)``.

        A bcrypt comparison always runs, against a dummy hash when there is no such user,
        so response time does not reveal whether the account exists. Keys other than the
        username and ``password`` are extra constraints (``{"active": 1}``); keys starting
        with an underscore (``_token``) are ignored.
        """
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        if not isinstance(credentials, dict):
            return None, False
        password = credentials.get("password")
        identifier = credentials.get(self._username)
        if not isinstance(password, str) or not isinstance(identifier, str) or identifier == "":
            return None, False

        extra: dict[str, Any] = {}
        for key, value in credentials.items():
            if key in ("password", self._username) or (isinstance(key, str) and key.startswith("_")):
                continue
            if not isinstance(key, str) or not _COLUMN_RE.fullmatch(key):
                raise ValueError(f"Invalid credential key: {key!r}")
            if value is None or not isinstance(value, (str, int, float, bool)):
                verify_password(password, _dummy_hash())
                return None, False
            extra[key] = value

        query = self._model.where(self._username, identifier)
        for key, value in extra.items():
            query = query.where(key, value)
        user = query.first()
        if user is None:
            verify_password(password, _dummy_hash())
            return None, False
        hashed = user._attributes.get("password") or ""
        if not isinstance(hashed, str) or not verify_password(password, hashed):
            return user, False
        return user, True

    def _rehash_if_needed(self, user: Any, password: str) -> None:
        """Upgrade a legacy or weaker hash now that the plain password is known to be right."""
        try:
            stored = user._attributes.get("password") or ""
            if isinstance(stored, str) and stored and password_needs_rehash(stored):
                key = getattr(user, "primary_key", "id") or "id"
                type(user).where(key, user._attributes[key]).update({"password": hash_password(password)})
        except Exception:
            _log.warning("Could not upgrade the password hash for user %s", user._attributes.get("id"), exc_info=True)

    def check(self) -> bool:
        return self.user() is not None

    def guest(self) -> bool:
        return not self.check()

    def attempt(self, credentials: dict[str, Any]) -> bool:
        """Check credentials and log the user in. Returns True only when fully logged in.

        False means either bad credentials or that a second factor is required; call
        :meth:`two_factor_pending` to tell the two apart.
        """
        user, ok = self._retrieve_by_credentials(credentials)
        if user is None or not ok:
            return False
        self._rehash_if_needed(user, credentials["password"])
        if user._attributes.get("two_factor_enabled"):
            session = _get_session()
            if session is None:
                raise RuntimeError("Session middleware is not active.")
            session.regenerate()
            session.put("_2fa_pending", user._attributes["id"])
            session.put("_2fa_pending_at", int(time.time()))
            session.forget("_2fa_attempts")
            return False
        self.login(user)
        return True

    def two_factor_pending(self) -> bool:
        """Return True if a 2FA challenge is awaiting completion (and has not expired)."""
        session = _get_session()
        if not session or session.get("_2fa_pending") is None:
            return False
        from hunt.auth.two_factor import PENDING_TTL

        started = session.get("_2fa_pending_at")
        if isinstance(started, bool) or not isinstance(started, (int, float)):
            return False
        return time.time() - started <= PENDING_TTL

    def login(self, user: Any) -> None:
        session = _get_session()
        if session is None:
            raise RuntimeError("Session middleware is not active.")
        session.regenerate()
        # A login is a privilege change: drop any half-finished 2FA state and rotate the
        # CSRF token so a token planted or read before login is useless afterwards.
        for key in _TWO_FACTOR_SESSION_KEYS:
            session.forget(key)
        session.forget("_csrf_token")
        session.put(self._session_key, user._attributes["id"])
        try:
            from hunt.session.registry import SessionRegistry

            req = _get_current_request()
            ip = req.ip if req is not None else None
            ua = (req.header("user-agent", "") or "") if req is not None else ""
            SessionRegistry().register(session.id, user._attributes["id"], self._name, ip, ua)
        except Exception:
            # The login itself stands, but a session that is not in the registry cannot
            # be listed or revoked later, so this must not go unnoticed.
            _log.exception("Could not register the session of user %s in the session registry", user._attributes["id"])

    def login_using_id(self, user_id: Any) -> Any | None:
        """Fetch a user by primary key and log them in. Returns the user or None."""
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        user = self._model.find(user_id)
        if user is None:
            return None
        self.login(user)
        return user

    def once(self, credentials: dict[str, Any], *, allow_two_factor: bool = False) -> bool:
        """Authenticate for the current request only — no session is written.

        Password-only authentication would bypass the second factor, so this fails for a
        user with two-factor authentication enabled unless ``allow_two_factor=True`` is
        passed by a caller that verifies the second factor itself.
        """
        user, ok = self._retrieve_by_credentials(credentials)
        if user is None or not ok:
            return False
        if user._attributes.get("two_factor_enabled") and not allow_two_factor:
            return False
        _set_once_user(self._name, user)
        return True

    def once_using_id(self, user_id: Any) -> Any | None:
        """Authenticate a specific user for the current request only."""
        if self._model is None:
            raise RuntimeError(f"Guard '{self._name}': model not configured.")
        user = self._model.find(user_id)
        if user is None:
            return None
        _set_once_user(self._name, user)
        return user

    def logout(self) -> None:
        users = _once_user_var.get()
        if isinstance(users, dict) and self._name in users:
            _once_user_var.set({k: v for k, v in users.items() if k != self._name})
        session = _get_session()
        if session:
            old_id = session.id
            try:
                from hunt.session.registry import SessionRegistry

                if old_id:
                    SessionRegistry().deregister(old_id)
            except Exception:
                _log.exception("Could not remove a session from the session registry on logout")
            session.forget(self._session_key)
            try:
                remaining = [k for k in session.all() if isinstance(k, str) and k.startswith("_auth_id")]
            except Exception:
                remaining = []
            if remaining:
                # Another guard is still logged in on this session: remove only what
                # belongs to the login that is ending.
                for key in _TWO_FACTOR_SESSION_KEYS:
                    session.forget(key)
                session.forget("_csrf_token")
            else:
                # Nothing else is authenticated: drop everything (pending 2FA state, CSRF
                # token, application data) except messages flashed for the next request.
                flash_new = session.get("_flash_new")
                session.flush()
                if isinstance(flash_new, dict) and flash_new:
                    session.put("_flash_new", flash_new)
            session.regenerate()


class _TokenGuard:
    """Auth guard backed by a static Bearer token.

    Reading the token from the query string is off by default because URLs end up in
    access logs, browser history and ``Referer`` headers. Opt in per guard with
    ``{"driver": "token", "allow_query": True}``.
    """

    def __init__(
        self,
        name: str,
        model: type | None = None,
        field: str = "api_token",
        allow_query: bool = False,
    ) -> None:
        self._name = name
        self._model = model
        self._field = field
        self._allow_query = bool(allow_query)
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
        if not self._allow_query:
            return None
        return req.query(self._field)

    def user(self) -> Any | None:
        token = self._extract_token()
        if not token or not isinstance(token, str) or self._model is None:
            return None
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
                g = _TokenGuard(name, model, field, allow_query=bool(config.get("allow_query", False)))
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
        """Log in with credentials. False means bad credentials *or* a pending second factor;
        check :meth:`two_factor_pending` to distinguish them."""
        return self._default_guard.attempt(credentials)

    def two_factor_pending(self) -> bool:
        """True when the last attempt passed the password check and awaits a 2FA code."""
        pending = getattr(self._default_guard, "two_factor_pending", None)
        return bool(pending()) if callable(pending) else False

    def login(self, user: Any) -> None:
        self._default_guard.login(user)

    def login_using_id(self, user_id: Any) -> Any | None:
        return self._default_guard.login_using_id(user_id)

    def once(self, credentials: dict[str, Any], *, allow_two_factor: bool = False) -> bool:
        return self._default_guard.once(credentials, allow_two_factor=allow_two_factor)

    def once_using_id(self, user_id: Any) -> Any | None:
        return self._default_guard.once_using_id(user_id)

    def logout(self) -> None:
        self._default_guard.logout()


Auth = _AuthManager()


# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------

# bcrypt only reads the first 72 bytes of its input, so two long passphrases sharing a
# 72-byte prefix would be interchangeable (and bcrypt >= 5 refuses longer input outright).
# New hashes therefore feed bcrypt a fixed-length SHA-256 digest of the password and are
# stored with this prefix. Hashes without the prefix are legacy plain-bcrypt hashes; they
# keep verifying and are upgraded on the next successful login.
PASSWORD_HASH_PREFIX = "$hunt-sha256"
_BCRYPT_MAX_BYTES = 72
_DEFAULT_ROUNDS = 12
_MIN_ROUNDS = 4
_MAX_ROUNDS = 31
_BCRYPT_RE = re.compile(r"\$2[abxy]?\$(\d{2})\$[./A-Za-z0-9]{53}")

_dummy_hash_value: str | None = None


def bcrypt_rounds() -> int:
    """Configured bcrypt work factor: config ``hashing.rounds``, else ``BCRYPT_ROUNDS``, else 12."""
    value: Any = None
    try:
        from hunt.support.helpers import config

        value = config("hashing.rounds")
    except Exception:
        value = None
    if value is None:
        value = os.environ.get("BCRYPT_ROUNDS")
    try:
        rounds = int(value) if value is not None and not isinstance(value, bool) else _DEFAULT_ROUNDS
    except (TypeError, ValueError):
        rounds = _DEFAULT_ROUNDS
    return max(_MIN_ROUNDS, min(_MAX_ROUNDS, rounds))


def _prehash(password: str) -> bytes:
    """44 ASCII bytes that depend on every byte of the password (and never contain NUL)."""
    return base64.b64encode(hashlib.sha256(password.encode("utf-8", "surrogatepass")).digest())


def hash_password(password: str, rounds: int | None = None) -> str:
    import bcrypt

    if not isinstance(password, str):
        raise TypeError("password must be a string")
    cost = bcrypt_rounds() if rounds is None else max(_MIN_ROUNDS, min(_MAX_ROUNDS, int(rounds)))
    return PASSWORD_HASH_PREFIX + bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=cost)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt

    if not isinstance(plain, str) or not isinstance(hashed, str) or not hashed:
        return False
    try:
        if hashed.startswith(PASSWORD_HASH_PREFIX):
            return bcrypt.checkpw(_prehash(plain), hashed[len(PASSWORD_HASH_PREFIX) :].encode())
        # Legacy hash made by plain bcrypt, which silently used only the first 72 bytes.
        # Truncate explicitly so these keep verifying on bcrypt >= 5, which raises instead.
        return bcrypt.checkpw(plain.encode("utf-8", "surrogatepass")[:_BCRYPT_MAX_BYTES], hashed.encode())
    except Exception:
        return False


def password_needs_rehash(hashed: str, rounds: int | None = None) -> bool:
    """True when a stored hash should be replaced: legacy format, or a different work factor."""
    if not isinstance(hashed, str) or not hashed.startswith(PASSWORD_HASH_PREFIX):
        return True
    match = _BCRYPT_RE.fullmatch(hashed[len(PASSWORD_HASH_PREFIX) :])
    if match is None:
        return True
    return int(match.group(1)) != (bcrypt_rounds() if rounds is None else int(rounds))


def _dummy_hash() -> str:
    """A real hash of a random password, used to spend the same time when no user is found."""
    global _dummy_hash_value
    if _dummy_hash_value is None:
        _dummy_hash_value = hash_password(os.urandom(16).hex())
    return _dummy_hash_value


_TWO_FACTOR_SESSION_KEYS = ("_2fa_pending", "_2fa_pending_at", "_2fa_attempts", "_2fa_pending_secret")
