from __future__ import annotations

import time
from typing import Any


class _EmailVerification:
    """Generate and verify signed email verification URLs."""

    EXPIRY = 3600  # seconds

    PURPOSE = "email-verify"

    def verification_url(self, user: Any, base_url: str = "") -> str:
        """Return a signed URL the user must visit to verify their email.

        The URL carries only the user ID and the expiry time; the email is part of
        the HMAC payload but not the URL, preventing PII leakage via server
        logs and Referer headers. The signature uses a key derived for this purpose
        only, so it cannot be exchanged with any other signed value.
        """
        from hunt.security.signing import sign

        user_id = user._attributes.get("id")
        email = str(user._attributes.get("email", ""))
        expires = int(time.time()) + self.EXPIRY
        signed_payload = f"{user_id}:{email}:{expires}"
        sig = sign(signed_payload, purpose=self.PURPOSE)
        return f"{base_url}/email/verify?id={user_id}&expires={expires}&signature={sig}"

    def verify(self, user: Any, expires: str, signature: str) -> bool:
        """Validate the signed token and mark the user's email as verified.

        ``expires`` is the expiry timestamp from the URL. Returns True on success, False
        if the token is invalid or expired. The email is reconstructed from the user
        object (never from the URL). A valid link for an already verified user returns
        True without writing again.
        """
        from hunt.security.signing import verify as _verify_sig

        user_id = str(user._attributes.get("id", ""))
        email = str(user._attributes.get("email", ""))
        try:
            expiry = int(expires)
        except (ValueError, TypeError):
            return False
        if not isinstance(signature, str):
            return False
        signed_payload = f"{user_id}:{email}:{expiry}"
        if not _verify_sig(signed_payload, signature, purpose=self.PURPOSE):
            return False
        now = int(time.time())
        if expiry < now or expiry > now + self.EXPIRY:
            return False
        if self.is_verified(user):
            return True

        user._attributes["email_verified_at"] = now
        user.save()
        return True

    def is_verified(self, user: Any) -> bool:
        """Return True if the user's email has been verified."""
        return bool(user._attributes.get("email_verified_at"))

    def resend(self, user: Any, base_url: str = "") -> str:
        """Generate a fresh verification URL."""
        return self.verification_url(user, base_url)


EmailVerification = _EmailVerification()
