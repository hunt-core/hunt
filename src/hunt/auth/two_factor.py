from __future__ import annotations

import os
import secrets


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
