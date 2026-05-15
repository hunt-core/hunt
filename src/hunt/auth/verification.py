from __future__ import annotations

import time
from typing import Any


class _EmailVerification:
    """Generate and verify signed email verification URLs."""

    EXPIRY = 3600  # seconds

    def verification_url(self, user: Any, base_url: str = "") -> str:
        """Return a signed URL the user must visit to verify their email.

        The URL carries only the user ID and timestamp; the email is part of
        the HMAC payload but not the URL, preventing PII leakage via server
        logs and Referer headers.
        """
        from hunt.security.signing import sign

        user_id = user._attributes.get("id")
        email = str(user._attributes.get("email", ""))
        ts = int(time.time())
        signed_payload = f"{user_id}:{email}:{ts}"
        sig = sign(signed_payload)
        return f"{base_url}/email/verify?id={user_id}&expires={ts}&signature={sig}"

    def verify(self, user: Any, expires: str, signature: str) -> bool:
        """Validate the signed token and mark the user's email as verified.

        Returns True on success, False if the token is invalid or expired.
        The email is reconstructed from the user object (never from the URL).
        """
        from hunt.security.signing import verify as _verify_sig

        user_id = str(user._attributes.get("id", ""))
        email = str(user._attributes.get("email", ""))
        try:
            ts = int(expires)
        except (ValueError, TypeError):
            return False
        signed_payload = f"{user_id}:{email}:{ts}"
        if not _verify_sig(signed_payload, signature):
            return False
        if ts + self.EXPIRY < int(time.time()):
            return False

        user._attributes["email_verified_at"] = int(time.time())
        user.save()
        return True

    def is_verified(self, user: Any) -> bool:
        """Return True if the user's email has been verified."""
        return bool(user._attributes.get("email_verified_at"))

    def resend(self, user: Any, base_url: str = "") -> str:
        """Generate a fresh verification URL."""
        return self.verification_url(user, base_url)


EmailVerification = _EmailVerification()
