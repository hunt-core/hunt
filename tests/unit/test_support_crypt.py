from __future__ import annotations

import pytest


class TestCrypt:
    def test_encrypt_decrypt_roundtrip(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-key-for-crypt-tests")
        from hunt.support.crypt import decrypt, encrypt

        plaintext = "super-secret-value"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_encrypt_is_not_plaintext(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-key-for-crypt-tests")
        from hunt.support.crypt import encrypt

        value = "mysecret"
        assert value not in encrypt(value)

    def test_two_encryptions_differ(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-key-for-crypt-tests")
        from hunt.support.crypt import encrypt

        value = "same-value"
        assert encrypt(value) != encrypt(value)

    def test_decrypt_fails_with_wrong_key(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "key-one")
        from hunt.support.crypt import encrypt

        token = encrypt("secret")

        monkeypatch.setenv("APP_KEY", "key-two")
        from cryptography.fernet import InvalidToken
        from hunt.support.crypt import decrypt

        with pytest.raises(InvalidToken):
            decrypt(token)

    def test_raises_without_app_key(self, monkeypatch):
        monkeypatch.delenv("APP_KEY", raising=False)
        from hunt.support.crypt import encrypt

        with pytest.raises(RuntimeError, match="APP_KEY"):
            encrypt("anything")
