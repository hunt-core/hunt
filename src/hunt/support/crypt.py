from __future__ import annotations

import base64
import hashlib
import os


def _fernet_key() -> bytes:
    app_key = os.environ.get("APP_KEY", "")
    if not app_key:
        raise RuntimeError("APP_KEY is not set. Cannot encrypt/decrypt values — set APP_KEY in your .env file.")
    return base64.urlsafe_b64encode(hashlib.sha256(app_key.encode()).digest())


def encrypt(value: str) -> str:
    """Encrypt a plaintext string using Fernet (AES-128-CBC + HMAC), keyed to APP_KEY."""
    from cryptography.fernet import Fernet

    return Fernet(_fernet_key()).encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token produced by :func:`encrypt`.

    Raises ``cryptography.fernet.InvalidToken`` if the token is invalid or
    was encrypted with a different APP_KEY.
    """
    from cryptography.fernet import Fernet

    return Fernet(_fernet_key()).decrypt(token.encode()).decode()
