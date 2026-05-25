from __future__ import annotations

import base64
import hashlib
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

    # ------------------------------------------------------------------
    # TOTP secret encryption (Fernet, keyed to APP_KEY)
    # ------------------------------------------------------------------

    @staticmethod
    def _fernet_key() -> bytes:
        from cryptography.fernet import Fernet  # noqa: F401 — validates availability

        app_key = os.environ.get("APP_KEY", "")
        return base64.urlsafe_b64encode(hashlib.sha256(app_key.encode()).digest())

    @staticmethod
    def encrypt_secret(secret: str) -> str:
        from cryptography.fernet import Fernet

        return Fernet(TwoFactor._fernet_key()).encrypt(secret.encode()).decode()

    @staticmethod
    def decrypt_secret(token: str) -> str:
        from cryptography.fernet import Fernet

        return Fernet(TwoFactor._fernet_key()).decrypt(token.encode()).decode()

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
