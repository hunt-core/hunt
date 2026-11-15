"""HMAC signing utilities using APP_KEY."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_MIN_KEY_LENGTH = 32


def app_key_bytes() -> bytes:
    """Return the decoded APP_KEY as raw bytes, validating its length.

    Handles the ``base64:`` prefix written by ``hunt key:generate`` and enforces
    a 32-byte minimum. This is the single source of truth for the application key
    used by both HMAC signing and value encryption (``hunt.support.crypt``).
    """
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


# Backwards-compatible private alias.
_get_app_key = app_key_bytes


def _signing_key(purpose: str | None) -> bytes:
    """Return the HMAC key for *purpose*.

    ``None`` uses APP_KEY directly (the original behaviour). A purpose derives
    an independent sub-key, ``HMAC(APP_KEY, b"hunt." + purpose)``, so that a
    signature issued for one feature cannot be replayed against another.
    """
    key = _get_app_key()
    if purpose is None:
        return key
    return hmac.new(key, b"hunt." + purpose.encode(), hashlib.sha256).digest()


def sign(payload: str, *, purpose: str | None = None) -> str:
    """Return an HMAC-SHA256 hex digest for the given payload string.

    Pass *purpose* (for example ``"queue.v1"`` or ``"email-verify"``) to sign
    with a key derived for that purpose only.
    """
    return hmac.new(_signing_key(purpose), payload.encode(), hashlib.sha256).hexdigest()


def verify(payload: str, signature: str, *, purpose: str | None = None) -> bool:
    """Return True if signature matches the HMAC of payload for *purpose*."""
    try:
        expected = sign(payload, purpose=purpose)
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False
