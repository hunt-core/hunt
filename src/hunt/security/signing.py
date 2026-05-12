"""HMAC signing utilities using APP_KEY."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os


def _get_app_key() -> bytes:
    key = os.environ.get("APP_KEY", "")
    if not key:
        raise RuntimeError(
            "APP_KEY is not set. Run `hunt key:generate` to generate one."
        )
    if key.startswith("base64:"):
        raw = base64.urlsafe_b64decode(key[7:] + "==")
        return raw
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
