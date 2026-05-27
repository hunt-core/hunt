from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# TOTP replay protection store
# ---------------------------------------------------------------------------

# Each verified code is stored for this long (valid_window=1 covers 3x 30s steps).
_TOTP_REPLAY_TTL = 90

# In-memory fallback used when Redis is unavailable (single-process only).
_used_codes: dict[str, float] = {}
_used_codes_lock = threading.Lock()


def _mem_mark(key: str) -> None:
    expiry = time.monotonic() + _TOTP_REPLAY_TTL
    with _used_codes_lock:
        now = time.monotonic()
        expired = [k for k, v in _used_codes.items() if v <= now]
        for k in expired:
            del _used_codes[k]
        _used_codes[key] = expiry


def _mem_is_used(key: str) -> bool:
    with _used_codes_lock:
        expiry = _used_codes.get(key)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            del _used_codes[key]
            return False
        return True


class TwoFactor:
    """TOTP-based two-factor authentication helpers."""

    @staticmethod
    def generate_secret() -> str:
        import pyotp

        return pyotp.random_base32()

    @staticmethod
    def qr_code_url(secret: str, email: str, app_name: str | None = None) -> str:
        import pyotp

        name = app_name or os.environ.get("APP_NAME", "hunt")
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=email, issuer_name=name)

    @staticmethod
    def verify(secret: str, code: str) -> bool:
        import pyotp

        totp = pyotp.TOTP(secret)
        return totp.verify(code.replace(" ", ""), valid_window=1)

    @staticmethod
    def generate_recovery_codes(n: int = 8) -> list[str]:
        return [secrets.token_hex(5) + "-" + secrets.token_hex(5) for _ in range(n)]

    # ------------------------------------------------------------------
    # Replay protection
    # ------------------------------------------------------------------

    @staticmethod
    def mark_used(user_id: Any, code: str) -> None:
        """Record that this TOTP code has been used for the given user.

        Prevents the same code from being accepted a second time within the
        valid window (~90 s).  Persists to Redis when available; falls back to
        a process-local in-memory store otherwise.
        """
        key = f"hunt:2fa:used:{user_id}:{code}"
        try:
            from hunt.redis_connection import get_redis

            get_redis().set(key, "1", ex=_TOTP_REPLAY_TTL)
            return
        except Exception:
            pass
        _mem_mark(key)

    @staticmethod
    def is_used(user_id: Any, code: str) -> bool:
        """Return True if this code was already accepted for the given user."""
        key = f"hunt:2fa:used:{user_id}:{code}"
        try:
            from hunt.redis_connection import get_redis

            return bool(get_redis().get(key))
        except Exception:
            pass
        return _mem_is_used(key)

    # ------------------------------------------------------------------
    # TOTP secret encryption (delegated to hunt.support.crypt)
    # ------------------------------------------------------------------

    @staticmethod
    def encrypt_secret(secret: str) -> str:
        from hunt.support.crypt import encrypt

        return encrypt(secret)

    @staticmethod
    def decrypt_secret(token: str) -> str:
        from hunt.support.crypt import decrypt

        return decrypt(token)

    # ------------------------------------------------------------------
    # Recovery code hashing (bcrypt, single-use)
    # ------------------------------------------------------------------

    @staticmethod
    def hash_recovery_code(code: str) -> str:
        import bcrypt

        return bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_recovery_code(code: str, hashed: str) -> bool:
        import bcrypt

        try:
            return bcrypt.checkpw(code.encode(), hashed.encode())
        except Exception:
            return False
