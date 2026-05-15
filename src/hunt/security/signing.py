"""HMAC signing utilities using APP_KEY."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_MIN_KEY_LENGTH = 32


def _get_app_key() -> bytes:
    key = os.environ.get("APP_KEY", "")
    if not key:
        raise RuntimeError("APP_KEY is not set. Run `hunt key:generate` to generate one.")
    if key.startswith("base64:"):
        raw = base64.urlsafe_b64decode(key[7:] + "==")
        if len(raw) < _MIN_KEY_LENGTH:
            raise RuntimeError(f"APP_KEY is too short ({len(raw)} bytes). Must be at least {_MIN_KEY_LENGTH} bytes.")
        return raw
    if len(key) < _MIN_KEY_LENGTH:
        raise RuntimeError(
            f"APP_KEY is too short ({len(key)} chars). Must be at least {_MIN_KEY_LENGTH} characters, "
            "or use `hunt key:generate` to generate a secure base64 key."
        )
    return key.encode()


def sign(payload: str) -> str:
    """Return an HMAC-SHA256 hex digest for the given payload string."""
    key = _get_app_key()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def verify(payload: str, signature: str) -> bool:
    """Return True if signature matches the HMAC of payload."""
    try:
        expected = sign(payload)
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False
