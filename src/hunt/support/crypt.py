from __future__ import annotations

import base64
import hashlib
import os


def _fernet_key() -> bytes:
    # Derive the Fernet key from the validated, decoded APP_KEY bytes so that
    # encryption uses the same key material (and length/format checks) as HMAC
    # signing. Respects the base64: prefix and the 32-byte minimum.
    from hunt.security.signing import app_key_bytes

    return base64.urlsafe_b64encode(hashlib.sha256(app_key_bytes()).digest())


def _legacy_fernet_key() -> bytes | None:
    """Pre-0.4.x key derivation: SHA-256 of the raw APP_KEY string.

    Differs from :func:`_fernet_key` only when APP_KEY carries a ``base64:``
    prefix (the format produced by ``hunt key:generate``), in which case the
    old code hashed the literal prefixed string. Returned for decrypt fallback
    so values encrypted before the unification still decrypt.
    """
    app_key = os.environ.get("APP_KEY", "")
    if not app_key:
        return None
    legacy = base64.urlsafe_b64encode(hashlib.sha256(app_key.encode()).digest())
    if legacy == _fernet_key():
        return None  # No prefix → identical key, no fallback needed.
    return legacy


def encrypt(value: str) -> str:
    """Encrypt a plaintext string using Fernet (AES-128-CBC + HMAC), keyed to APP_KEY."""
    from cryptography.fernet import Fernet

    return Fernet(_fernet_key()).encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token produced by :func:`encrypt`.

    Tries the current key first, then the pre-0.4.x legacy key derivation so
    values encrypted before the key-handling unification still decrypt.

    Raises ``cryptography.fernet.InvalidToken`` if the token is invalid or was
    encrypted with a different APP_KEY.
    """
    from cryptography.fernet import Fernet, InvalidToken

    raw = token.encode()
    try:
        return Fernet(_fernet_key()).decrypt(raw).decode()
    except InvalidToken:
        legacy = _legacy_fernet_key()
        if legacy is None:
            raise
        return Fernet(legacy).decrypt(raw).decode()
