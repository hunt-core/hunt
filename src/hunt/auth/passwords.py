from __future__ import annotations

import logging
import os
import time
from typing import Any

_log = logging.getLogger("hunt.auth")


class PasswordBroker:
    """Generate, validate, and consume password reset tokens."""

    TABLE = "password_reset_tokens"
    TOKEN_EXPIRY = 3600  # seconds
    # Minimum seconds between two reset links for the same address. Without it anyone can
    # keep invalidating a victim's fresh link and flood their mailbox.
    THROTTLE = 60
    MIN_PASSWORD_LENGTH = 8

    def __init__(self) -> None:
        self._model: type | None = None

    def set_model(self, model: type) -> None:
        self._model = model

    def create_token(self, email: str) -> tuple[str, str] | None:
        """Create a reset token. Returns ``(stored_email, token)`` or None.

        ``stored_email`` is the address on the user record, which is where the token must
        be mailed. It can differ from the submitted one when the database collation
        treats look-alike addresses as equal, so never mail the token to the submitted
        value. None is returned when no user matches or when a link was issued for this
        address less than :attr:`THROTTLE` seconds ago (the previous link stays valid).

        Token generation and hashing always run before the user lookup so that
        response time does not reveal whether the email is registered.
        """
        return self._issue(email, exact=False)

    def send_reset_link(self, email: str) -> str | None:
        """Generate a token for the email and store it. Returns the raw token.

        The caller is responsible for sending the token to the user. Returns None when
        no user has exactly that email address, or when a link was requested for it less
        than :attr:`THROTTLE` seconds ago. Because the caller mails the token to the
        address it submitted, the submitted address must equal the stored one exactly
        (apart from ASCII letter case); use :meth:`create_token` to get the stored address.
        """
        created = self._issue(email, exact=True)
        return created[1] if created else None

    def _issue(self, email: Any, *, exact: bool) -> tuple[str, str] | None:
        token = os.urandom(32).hex()
        hashed = self._hash(token)
        if not isinstance(email, str) or not email:
            return None
        user = self._find_user(email)
        stored_email = str(user._attributes.get("email") or "") if user else ""
        if exact and user is not None and not self._same_address(email, stored_email):
            # The collation matched a look-alike address: behave as for an unknown one and
            # leave the real user's pending token alone.
            self._get_row(email)
            return None
        key = stored_email or email
        row = self._get_row(key)
        if row is not None and self._age(row) < self.THROTTLE:
            return None
        # Always delete to equalise DB round-trips regardless of whether the email exists.
        self._delete_existing(key)
        if user is None or not stored_email:
            return None
        self._insert_token(stored_email, hashed)
        return stored_email, token

    def token_valid(self, email: str, token: str) -> bool:
        """Return True if the token matches and has not expired."""
        return self._matching_row(email, token) is not None

    def reset(self, credentials: dict[str, Any], *, revoke_sessions: bool = True) -> bool:
        """Validate token then update the user's password. Returns True on success.

        The token is consumed atomically, so it works exactly once even under concurrent
        requests. Passwords shorter than :attr:`MIN_PASSWORD_LENGTH` are refused without
        consuming the token. All existing sessions of the user are revoked afterwards;
        pass ``revoke_sessions=False`` to keep other devices logged in.
        """
        email = credentials.get("email", "")
        token = credentials.get("token", "")
        password = credentials.get("password", "")

        if not isinstance(email, str) or not isinstance(token, str) or not isinstance(password, str):
            return False
        if len(password) < self.MIN_PASSWORD_LENGTH:
            return False

        row = self._matching_row(email, token)
        if row is None:
            return False

        user = self._find_user(email)
        if user is None or str(user._attributes.get("email") or "") != str(row["email"]):
            return False

        from hunt.auth.manager import hash_password

        new_hash = hash_password(password)
        from hunt.database.connection import begin

        # One transaction: the token is consumed and the password changed
        # together, or neither happens. If saving the user fails, the rollback
        # puts the token back so the link still works.
        with begin():
            # Single-use: only the request whose DELETE removes the row may change the password.
            if not self._consume(str(row["email"]), str(row["token"])):
                return False

            user._attributes["password"] = new_hash
            user.timestamps = False  # avoid touching updated_at unexpectedly
            if user.save() is False:
                raise RuntimeError("Password reset could not update the user record.")

        if revoke_sessions:
            try:
                from hunt.session.registry import revoke_sessions_for

                revoke_sessions_for(user._attributes.get("id"))
            except Exception:
                _log.exception(
                    "Password was reset for user %s but existing sessions could not be revoked",
                    user._attributes.get("id"),
                )

        return True

    def delete_token(self, email: str) -> None:
        self._delete_existing(email)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_user(self, email: str) -> Any | None:
        if self._model is None:
            raise RuntimeError("PasswordBroker: call set_model() before use.")
        return self._model.where("email", email).first()

    @staticmethod
    def _same_address(submitted: str, stored: str) -> bool:
        if submitted == stored:
            return True
        return submitted.isascii() and stored.isascii() and submitted.lower() == stored.lower()

    @staticmethod
    def _age(row: dict) -> int:
        try:
            return int(time.time()) - int(row.get("created_at") or 0)
        except (TypeError, ValueError):
            return 1 << 62

    def _matching_row(self, email: Any, token: Any) -> dict | None:
        """Return the stored row when the token matches and has not expired."""
        if not isinstance(email, str) or not isinstance(token, str) or not email or not token:
            return None
        row = self._get_row(email)
        if row is None:
            return None
        if self._age(row) > self.TOKEN_EXPIRY:
            return None
        if not self._verify(token, str(row["token"])):
            return None
        return row

    def _consume(self, email: str, hashed: str) -> bool:
        """Delete the exact token row; True only for the caller that actually removed it."""
        from sqlalchemy import text

        from hunt.database.connection import connection, use_connection

        with use_connection(connection()) as conn:
            result = conn.execute(
                text(f"DELETE FROM {self.TABLE} WHERE email = :e AND token = :t"),
                {"e": email, "t": hashed},
            )
            return (result.rowcount or 0) > 0

    def _hash(self, token: str) -> str:
        import bcrypt

        return bcrypt.hashpw(token.encode(), bcrypt.gensalt()).decode()

    def _verify(self, token: str, hashed: str) -> bool:
        import bcrypt

        try:
            return bcrypt.checkpw(token.encode(), hashed.encode())
        except Exception:
            return False

    def _delete_existing(self, email: str) -> None:
        from sqlalchemy import text

        from hunt.database.connection import connection, use_connection

        with use_connection(connection()) as conn:
            conn.execute(text(f"DELETE FROM {self.TABLE} WHERE email = :e"), {"e": email})

    def _insert_token(self, email: str, hashed: str) -> None:
        from sqlalchemy import text

        from hunt.database.connection import connection, use_connection

        now = int(time.time())
        with use_connection(connection()) as conn:
            conn.execute(
                text(f"INSERT INTO {self.TABLE} (email, token, created_at) VALUES (:e, :t, :c)"),
                {"e": email, "t": hashed, "c": now},
            )

    def _get_row(self, email: str) -> dict | None:
        from sqlalchemy import text

        from hunt.database.connection import connection, use_connection

        with use_connection(connection()) as conn:
            result = conn.execute(
                text(f"SELECT * FROM {self.TABLE} WHERE email = :e"),
                {"e": email},
            )
            keys = list(result.keys())
            row = result.fetchone()
        if row is None:
            return None
        return dict(zip(keys, row, strict=False))


Password = PasswordBroker()
