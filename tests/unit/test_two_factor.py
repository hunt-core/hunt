from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hunt.auth.two_factor import TwoFactor


class TestTwoFactorService:
    def test_generate_secret_returns_base32_string(self):
        secret = TwoFactor.generate_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 16

    def test_two_secrets_are_different(self):
        assert TwoFactor.generate_secret() != TwoFactor.generate_secret()

    def test_qr_code_url_contains_issuer(self):
        secret = TwoFactor.generate_secret()
        url = TwoFactor.qr_code_url(secret, "user@example.com", "MyApp")
        assert "MyApp" in url
        assert "user%40example.com" in url or "user@example.com" in url

    def test_qr_code_url_uses_app_name_env(self, monkeypatch):
        monkeypatch.setenv("APP_NAME", "EnvApp")
        secret = TwoFactor.generate_secret()
        url = TwoFactor.qr_code_url(secret, "a@b.com")
        assert "EnvApp" in url

    def test_verify_valid_code(self):
        import pyotp

        secret = TwoFactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        assert TwoFactor.verify(secret, code) is True

    def test_verify_invalid_code(self):
        secret = TwoFactor.generate_secret()
        assert TwoFactor.verify(secret, "000000") is False

    def test_verify_strips_spaces(self):
        import pyotp

        secret = TwoFactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        spaced = code[:3] + " " + code[3:]
        assert TwoFactor.verify(secret, spaced) is True

    def test_generate_recovery_codes_count(self):
        codes = TwoFactor.generate_recovery_codes(8)
        assert len(codes) == 8

    def test_generate_recovery_codes_format(self):
        codes = TwoFactor.generate_recovery_codes(4)
        for code in codes:
            assert "-" in code
            parts = code.split("-")
            assert len(parts) == 2

    def test_recovery_codes_are_unique(self):
        codes = TwoFactor.generate_recovery_codes(8)
        assert len(set(codes)) == 8

    def test_custom_recovery_code_count(self):
        codes = TwoFactor.generate_recovery_codes(n=12)
        assert len(codes) == 12

    def test_hash_recovery_code_produces_bcrypt_hash(self):
        code = "abc12-def34"
        hashed = TwoFactor.hash_recovery_code(code)
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_verify_recovery_code_correct(self):
        code = "abc12-def34"
        hashed = TwoFactor.hash_recovery_code(code)
        assert TwoFactor.verify_recovery_code(code, hashed) is True

    def test_verify_recovery_code_wrong(self):
        hashed = TwoFactor.hash_recovery_code("abc12-def34")
        assert TwoFactor.verify_recovery_code("wrong-code1", hashed) is False

    def test_hash_recovery_code_salted(self):
        code = "abc12-def34"
        assert TwoFactor.hash_recovery_code(code) != TwoFactor.hash_recovery_code(code)

    def test_encrypt_decrypt_secret_roundtrip(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-app-key-for-unit-tests")
        secret = TwoFactor.generate_secret()
        encrypted = TwoFactor.encrypt_secret(secret)
        assert encrypted != secret
        assert TwoFactor.decrypt_secret(encrypted) == secret

    def test_encrypt_secret_is_not_plaintext(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-app-key-for-unit-tests")
        secret = TwoFactor.generate_secret()
        encrypted = TwoFactor.encrypt_secret(secret)
        assert secret not in encrypted

    def test_decrypt_fails_with_wrong_key(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "key-a")
        secret = TwoFactor.generate_secret()
        encrypted = TwoFactor.encrypt_secret(secret)
        monkeypatch.setenv("APP_KEY", "key-b")
        from cryptography.fernet import InvalidToken

        with pytest.raises(InvalidToken):
            TwoFactor.decrypt_secret(encrypted)


class TestSessionGuardTwoFactor:
    def _make_guard(self, user_attrs):
        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web")
        model = MagicMock()
        user = MagicMock()
        user._attributes = user_attrs
        model.where.return_value.first.return_value = user
        guard._model = model
        return guard, user

    def test_attempt_with_2fa_enabled_returns_false_and_sets_pending(self):
        import bcrypt

        from hunt.auth.manager import _request_var

        hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
        guard, _user = self._make_guard(
            {
                "id": 5,
                "email": "a@b.com",
                "password": hashed,
                "two_factor_enabled": True,
                "two_factor_secret": "BASE32SECRET",
            }
        )

        session = MagicMock()
        session.get.return_value = 5
        request = MagicMock()
        request._session = session
        token = _request_var.set(request)
        try:
            result = guard.attempt({"email": "a@b.com", "password": "secret"})
        finally:
            _request_var.reset(token)

        assert result is False
        session.put.assert_called_once_with("_2fa_pending", 5)

    def test_attempt_without_2fa_logs_in_directly(self):
        import bcrypt

        from hunt.auth.manager import _request_var

        hashed = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()
        guard, _user = self._make_guard(
            {
                "id": 3,
                "email": "a@b.com",
                "password": hashed,
                "two_factor_enabled": False,
            }
        )

        session = MagicMock()
        request = MagicMock()
        request._session = session
        token = _request_var.set(request)
        try:
            result = guard.attempt({"email": "a@b.com", "password": "secret"})
        finally:
            _request_var.reset(token)

        assert result is True
        session.put.assert_called_with("_auth_id", 3)

    def test_two_factor_pending_true_when_session_has_key(self):
        from hunt.auth.manager import _request_var, _SessionGuard

        guard = _SessionGuard("web")
        session = MagicMock()
        session.get.return_value = 7
        request = MagicMock()
        request._session = session
        token = _request_var.set(request)
        try:
            assert guard.two_factor_pending() is True
        finally:
            _request_var.reset(token)

    def test_two_factor_pending_false_when_no_session_key(self):
        from hunt.auth.manager import _request_var, _SessionGuard

        guard = _SessionGuard("web")
        session = MagicMock()
        session.get.return_value = None
        request = MagicMock()
        request._session = session
        token = _request_var.set(request)
        try:
            assert guard.two_factor_pending() is False
        finally:
            _request_var.reset(token)


class TestEnsureTwoFactorMiddleware:
    @pytest.mark.asyncio
    async def test_redirects_when_2fa_pending(self):
        from hunt.http.middleware.two_factor import EnsureTwoFactorAuthenticated

        session = MagicMock()
        session.get.return_value = 5
        request = MagicMock()
        request.path = "/dashboard"
        request._session = session

        middleware = EnsureTwoFactorAuthenticated()
        next_called = []

        async def next_fn(req):
            next_called.append(True)
            return MagicMock()

        response = await middleware.handle(request, next_fn)
        assert not next_called
        assert response.status == 302
        assert response._headers.get("Location") == "/two-factor/challenge"

    @pytest.mark.asyncio
    async def test_passes_through_on_challenge_path(self):
        from hunt.http.middleware.two_factor import EnsureTwoFactorAuthenticated

        session = MagicMock()
        session.get.return_value = 5
        request = MagicMock()
        request.path = "/two-factor/challenge"
        request._session = session

        middleware = EnsureTwoFactorAuthenticated()
        next_called = []

        async def next_fn(req):
            next_called.append(True)
            from hunt.http.response import Response

            return Response("ok")

        await middleware.handle(request, next_fn)
        assert next_called

    @pytest.mark.asyncio
    async def test_passes_through_when_no_pending(self):
        from hunt.http.middleware.two_factor import EnsureTwoFactorAuthenticated

        session = MagicMock()
        session.get.return_value = None
        request = MagicMock()
        request.path = "/dashboard"
        request._session = session

        middleware = EnsureTwoFactorAuthenticated()
        next_called = []

        async def next_fn(req):
            next_called.append(True)
            from hunt.http.response import Response

            return Response("ok")

        await middleware.handle(request, next_fn)
        assert next_called

    @pytest.mark.asyncio
    async def test_exempt_login_path(self):
        from hunt.http.middleware.two_factor import EnsureTwoFactorAuthenticated

        session = MagicMock()
        session.get.return_value = 5
        request = MagicMock()
        request.path = "/login"
        request._session = session

        middleware = EnsureTwoFactorAuthenticated()
        next_called = []

        async def next_fn(req):
            next_called.append(True)
            from hunt.http.response import Response

            return Response("ok")

        await middleware.handle(request, next_fn)
        assert next_called
