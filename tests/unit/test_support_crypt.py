from __future__ import annotations

import base64
import hashlib

import pytest

# Valid keys must be at least 32 bytes/chars (enforced via hunt.security.signing).
_KEY_ONE = "test-key-for-crypt-tests-0123456789"
_KEY_TWO = "a-different-key-for-crypt-tests-0123456789"


class TestCrypt:
    def test_encrypt_decrypt_roundtrip(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", _KEY_ONE)
        from hunt.support.crypt import decrypt, encrypt

        plaintext = "super-secret-value"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_encrypt_is_not_plaintext(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", _KEY_ONE)
        from hunt.support.crypt import encrypt

        value = "mysecret"
        assert value not in encrypt(value)

    def test_two_encryptions_differ(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", _KEY_ONE)
        from hunt.support.crypt import encrypt

        value = "same-value"
        assert encrypt(value) != encrypt(value)

    def test_decrypt_fails_with_wrong_key(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", _KEY_ONE)
        from hunt.support.crypt import encrypt

        token = encrypt("secret")

        monkeypatch.setenv("APP_KEY", _KEY_TWO)
        from cryptography.fernet import InvalidToken

        from hunt.support.crypt import decrypt

        with pytest.raises(InvalidToken):
            decrypt(token)

    def test_raises_without_app_key(self, monkeypatch):
        monkeypatch.delenv("APP_KEY", raising=False)
        from hunt.support.crypt import encrypt

        with pytest.raises(RuntimeError, match="APP_KEY"):
            encrypt("anything")

    def test_rejects_short_app_key(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "too-short")
        from hunt.support.crypt import encrypt

        with pytest.raises(RuntimeError, match="too short"):
            encrypt("anything")

    def test_roundtrip_with_base64_prefixed_key(self, monkeypatch):
        import os

        key = "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode()
        monkeypatch.setenv("APP_KEY", key)
        from hunt.support.crypt import decrypt, encrypt

        assert decrypt(encrypt("value")) == "value"

    def test_decrypts_legacy_base64_token(self, monkeypatch):
        """A value encrypted under the pre-0.4.x derivation still decrypts."""
        import os

        from cryptography.fernet import Fernet

        key = "base64:" + base64.urlsafe_b64encode(os.urandom(32)).decode()
        monkeypatch.setenv("APP_KEY", key)

        # Old derivation hashed the literal prefixed string.
        legacy_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
        legacy_token = Fernet(legacy_key).encrypt(b"legacy-value").decode()

        from hunt.support.crypt import decrypt

        assert decrypt(legacy_token) == "legacy-value"
