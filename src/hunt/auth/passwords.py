from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any


class PasswordBroker:
    """Generate, validate, and consume password reset tokens."""

    TABLE = "password_reset_tokens"
    TOKEN_EXPIRY = 3600  # seconds

    def __init__(self) -> None:
        self._model: type | None = None

    def set_model(self, model: type) -> None:
        self._model = model

    def send_reset_link(self, email: str) -> str | None:
        """Generate a token for the email and store it. Returns the raw token.

        The caller is responsible for sending the token to the user (e.g. via
        email once the mail system is implemented).  Returns None when no user
        with that email exists.
        """
        if not self._find_user(email):
            return None
        token = os.urandom(32).hex()
        self._delete_existing(email)
        self._insert_token(email, self._hash(token))
        return token

    def token_valid(self, email: str, token: str) -> bool:
        """Return True if the token matches and has not expired."""
        row = self._get_row(email)
        if row is None:
            return False
        expiry = int(row.get("created_at", 0)) + self.TOKEN_EXPIRY
        if expiry < int(time.time()):
            return False
        return hmac.compare_digest(self._hash(token), str(row["token"]))

    def reset(self, credentials: dict[str, Any]) -> bool:
        """Validate token then update the user's password. Returns True on success."""
        email = credentials.get("email", "")
        token = credentials.get("token", "")
        password = credentials.get("password", "")

        if not self.token_valid(email, token):
            return False

        user = self._find_user(email)
        if user is None:
            return False

        from hunt.auth.manager import hash_password

        user._attributes["password"] = hash_password(password)
        user.timestamps = False  # avoid touching updated_at unexpectedly
        user.save()
        self.delete_token(email)
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

    def _hash(self, token: str) -> str:
        # HMAC with APP_KEY so leaked DB hashes are useless without the key
        from hunt.security.signing import _get_app_key

        key = _get_app_key()
        return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()

    def _delete_existing(self, email: str) -> None:
        from sqlalchemy import text

        from hunt.database.connection import connection

        with connection().connect() as conn:
            conn.execute(text(f"DELETE FROM {self.TABLE} WHERE email = :e"), {"e": email})
            conn.commit()

    def _insert_token(self, email: str, hashed: str) -> None:
        from sqlalchemy import text

        from hunt.database.connection import connection

        now = int(time.time())
        with connection().connect() as conn:
            conn.execute(
                text(f"INSERT INTO {self.TABLE} (email, token, created_at) VALUES (:e, :t, :c)"),
                {"e": email, "t": hashed, "c": now},
            )
            conn.commit()

    def _get_row(self, email: str) -> dict | None:
        from sqlalchemy import text

        from hunt.database.connection import connection

        with connection().connect() as conn:
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
