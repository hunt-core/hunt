"""Regression tests for AUTH-04, AUTH-05, AUTH-06 and AUTH-L1, L2, L4, L5, L6."""

from __future__ import annotations

import os
import time
from typing import ClassVar
from unittest.mock import MagicMock, patch

import bcrypt
import pytest

os.environ.setdefault("DB_CONNECTION", "sqlite")
os.environ.setdefault("DB_DATABASE", ":memory:")

from hunt.auth import manager as mgr
from hunt.auth.manager import (
    PASSWORD_HASH_PREFIX,
    _AuthManager,
    _request_var,
    _SessionGuard,
    _set_request,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from hunt.auth.passwords import PasswordBroker
from hunt.database.connection import connection
from hunt.database.model import Model
from hunt.database.schema.builder import Schema


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    monkeypatch.setenv("BCRYPT_ROUNDS", "4")
    monkeypatch.setattr(mgr, "_dummy_hash_value", None)


class _Session:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.id = "sid-1"
        self.regenerated = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def put(self, key, value):
        self.data[key] = value

    def forget(self, key):
        self.data.pop(key, None)

    def all(self):
        return dict(self.data)

    def flush(self):
        self.data = {}

    def regenerate(self):
        self.regenerated += 1
        self.id = f"sid-{self.regenerated + 1}"
        return self.id


class _bound_request:
    """Bind a fake request (with a session) for the duration of a with-block."""

    def __init__(self, session: _Session | None = None) -> None:
        self.request = MagicMock()
        self.request._session = session
        self.request.ip = "127.0.0.1"
        self.request.header.return_value = "pytest"

    def __enter__(self):
        self._token = _request_var.set(self.request)
        return self.request

    def __exit__(self, *exc):
        _request_var.reset(self._token)
        mgr._once_user_var.set(None)


def _user(**attrs):
    user = MagicMock()
    user._attributes = {"id": 1, "email": "a@b.com", **attrs}
    return user


def _guard_with(user, name: str = "web") -> _SessionGuard:
    model = MagicMock()
    model.where.return_value.first.return_value = user
    model.where.return_value.where.return_value.first.return_value = user
    model.find.return_value = user
    return _SessionGuard(name, model)


# ---------------------------------------------------------------------------
# AUTH-04: bcrypt 72-byte truncation
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_roundtrip_and_prefix(self):
        hashed = hash_password("correct horse")
        assert hashed.startswith(PASSWORD_HASH_PREFIX + "$2")
        assert verify_password("correct horse", hashed) is True
        assert verify_password("wrong horse", hashed) is False

    def test_72_and_73_byte_passwords_are_different(self):
        p72 = "a" * 72
        p73 = "a" * 72 + "b"
        assert verify_password(p73, hash_password(p72)) is False
        assert verify_password(p72, hash_password(p73)) is False
        assert verify_password(p73, hash_password(p73)) is True

    def test_long_multibyte_passphrases_do_not_collide(self):
        base = "漢" * 24  # 72 UTF-8 bytes
        assert verify_password(base + "字", hash_password(base + "語")) is False
        assert verify_password(base + "語", hash_password(base + "語")) is True

    def test_very_long_password_does_not_raise(self):
        long_pw = "x" * 5000
        assert verify_password(long_pw, hash_password(long_pw)) is True

    def test_legacy_plain_bcrypt_hash_still_verifies(self):
        legacy = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
        assert verify_password("secret", legacy) is True
        assert verify_password("Secret", legacy) is False

    def test_legacy_hash_of_long_password_verifies_on_any_bcrypt(self):
        # bcrypt < 5 hashed only the first 72 bytes; bcrypt >= 5 raises on longer input.
        long_pw = "p" * 100
        legacy = bcrypt.hashpw(long_pw.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
        assert verify_password(long_pw, legacy) is True

    def test_legacy_verify_survives_bcrypt_5_behaviour(self):
        legacy = bcrypt.hashpw(b"p" * 72, bcrypt.gensalt(rounds=4)).decode()
        real_checkpw = bcrypt.checkpw

        def strict_checkpw(password, hashed):
            if len(password) > 72:
                raise ValueError("password cannot be longer than 72 bytes")
            return real_checkpw(password, hashed)

        with patch("bcrypt.checkpw", side_effect=strict_checkpw):
            assert verify_password("p" * 100, legacy) is True

    def test_prehash_never_exceeds_bcrypt_limit(self):
        assert len(mgr._prehash("x" * 10_000)) == 44
        assert b"\x00" not in mgr._prehash("\x00" * 10)

    @pytest.mark.parametrize("bad", [None, 123, b"bytes", ["x"]])
    def test_non_string_input_fails_closed(self, bad):
        hashed = hash_password("secret")
        assert verify_password(bad, hashed) is False
        assert verify_password("secret", bad) is False
        assert verify_password("secret", "") is False
        assert verify_password("secret", "not-a-hash") is False

    def test_needs_rehash(self, monkeypatch):
        legacy = bcrypt.hashpw(b"secret", bcrypt.gensalt(rounds=4)).decode()
        assert password_needs_rehash(legacy) is True
        current = hash_password("secret")
        assert password_needs_rehash(current) is False
        monkeypatch.setenv("BCRYPT_ROUNDS", "5")
        assert password_needs_rehash(current) is True
        assert password_needs_rehash(hash_password("secret")) is False
        assert password_needs_rehash("") is True
        assert password_needs_rehash(PASSWORD_HASH_PREFIX + "garbage") is True

    def test_rounds_are_configurable_and_clamped(self, monkeypatch):
        monkeypatch.setenv("BCRYPT_ROUNDS", "5")
        assert mgr.bcrypt_rounds() == 5
        assert hash_password("x")[len(PASSWORD_HASH_PREFIX) :].split("$")[2] == "05"
        monkeypatch.setenv("BCRYPT_ROUNDS", "1")
        assert mgr.bcrypt_rounds() == 4
        monkeypatch.setenv("BCRYPT_ROUNDS", "nonsense")
        assert mgr.bcrypt_rounds() == 12
        monkeypatch.delenv("BCRYPT_ROUNDS")
        assert mgr.bcrypt_rounds() == 12
        assert hash_password("x", rounds=4)[len(PASSWORD_HASH_PREFIX) :].split("$")[2] == "04"

    def test_helper_alias_uses_the_same_scheme(self):
        from hunt.support.helpers import hash_password as helper_hash

        assert verify_password("secret", helper_hash("secret")) is True


class FixLoginUser(Model):
    table = "fix_auth_login_users"
    fillable: ClassVar[list[str]] = ["email", "password", "active"]
    timestamps = False


class FixResetTokens(PasswordBroker):
    TABLE = "fix_auth_reset_tokens"


@pytest.fixture(scope="module", autouse=True)
def _tables():
    from sqlalchemy import text

    Schema.drop_if_exists("fix_auth_login_users")
    Schema.create(
        "fix_auth_login_users",
        lambda bp: [bp.id(), bp.string("email"), bp.string("password"), bp.integer("active").default(1)],
    )
    with connection().connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS fix_auth_reset_tokens"))
        conn.execute(
            text("CREATE TABLE fix_auth_reset_tokens (email VARCHAR(255), token VARCHAR(255), created_at INTEGER)")
        )
        conn.commit()
    yield
    Schema.drop_if_exists("fix_auth_login_users")
    with connection().connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS fix_auth_reset_tokens"))
        conn.commit()


class TestRehashOnLogin:
    def test_legacy_hash_is_upgraded_after_successful_login(self):
        legacy = bcrypt.hashpw(b"secret-pass", bcrypt.gensalt(rounds=4)).decode()
        created = FixLoginUser.create({"email": "legacy@example.com", "password": legacy})
        guard = _SessionGuard("web", FixLoginUser)
        with _bound_request(_Session()):
            assert guard.attempt({"email": "legacy@example.com", "password": "wrong"}) is False
            assert FixLoginUser.find(created._attributes["id"])._attributes["password"] == legacy
            assert guard.attempt({"email": "legacy@example.com", "password": "secret-pass"}) is True
        stored = FixLoginUser.find(created._attributes["id"])._attributes["password"]
        assert stored.startswith(PASSWORD_HASH_PREFIX)
        assert verify_password("secret-pass", stored) is True
        with _bound_request(_Session()):
            assert guard.attempt({"email": "legacy@example.com", "password": "secret-pass"}) is True
        assert FixLoginUser.find(created._attributes["id"])._attributes["password"] == stored

    def test_extra_credentials_are_constraints(self):  # AUTH-L6
        FixLoginUser.create({"email": "off@example.com", "password": hash_password("secret-pass"), "active": 0})
        guard = _SessionGuard("web", FixLoginUser)
        creds = {"email": "off@example.com", "password": "secret-pass"}
        with _bound_request(_Session()):
            assert guard.attempt({**creds, "active": 1}) is False
            assert guard.attempt({**creds, "active": 0, "_token": "csrf"}) is True

    def test_invalid_credential_key_raises(self):
        guard = _SessionGuard("web", FixLoginUser)
        with _bound_request(_Session()), pytest.raises(ValueError):
            guard.attempt({"email": "x@example.com", "password": "p", "1=1; --": "x"})


# ---------------------------------------------------------------------------
# AUTH-05: timing
# ---------------------------------------------------------------------------


class TestLoginTiming:
    def test_checkpw_runs_when_user_is_missing(self):
        guard = _guard_with(None)
        with _bound_request(_Session()), patch("bcrypt.checkpw", wraps=bcrypt.checkpw) as checkpw:
            assert guard.attempt({"email": "nobody@example.com", "password": "whatever"}) is False
            assert checkpw.call_count == 1
            assert guard.once({"email": "nobody@example.com", "password": "whatever"}) is False
            assert checkpw.call_count == 2

    def test_dummy_hash_is_created_once(self):
        first = mgr._dummy_hash()
        assert mgr._dummy_hash() is first
        assert first.startswith(PASSWORD_HASH_PREFIX)

    @pytest.mark.parametrize(
        "credentials",
        [
            {"email": ["a@b.com"], "password": "secret"},
            {"email": {"$ne": ""}, "password": "secret"},
            {"email": "a@b.com", "password": ["secret"]},
            {"email": "a@b.com", "password": None},
            {"email": "a@b.com"},
            {"email": None, "password": "secret"},
            {"email": 5, "password": "secret"},
            {"password": "secret"},
        ],
    )
    def test_non_string_credentials_fail_without_query(self, credentials):
        user = _user(password=hash_password("secret"))
        guard = _guard_with(user)
        with _bound_request(_Session()):
            assert guard.attempt(credentials) is False
            assert guard.once(credentials) is False
        guard._model.where.assert_not_called()


# ---------------------------------------------------------------------------
# AUTH-06: once user
# ---------------------------------------------------------------------------


class TestOnceUser:
    def test_once_user_is_scoped_to_its_guard(self):
        user = _user(id=5)
        api = _guard_with(user, "api")
        admin = _guard_with(None, "admin")
        web = _guard_with(None, "web")
        with _bound_request(_Session()):
            assert api.once_using_id(5) is user
            assert api.check() is True
            assert api.id() == 5
            assert admin.check() is False
            assert admin.user() is None
            assert web.id() is None

    def test_manager_guards_do_not_share_once_user(self):
        auth = _AuthManager()
        user = _user(id=5)
        model = MagicMock()
        model.find.return_value = user
        auth.configure({"web": {"driver": "session", "model": model}, "admin": {"driver": "session", "model": model}})
        with _bound_request(_Session()):
            auth.guard("admin").once_using_id(5)
            assert auth.guard("admin").check() is True
            assert auth.check() is False

    def test_once_user_is_cleared_for_the_next_request(self):
        user = _user(id=5)
        guard = _guard_with(user, "api")
        guard._model.find.return_value = user
        first = MagicMock()
        first._session = None
        token = _request_var.set(None)
        try:
            _set_request(first)
            guard.once_using_id(5)
            assert guard.check() is True
            second = MagicMock()
            second._session = None
            _set_request(second)  # same context reused (test client, queue worker)
            assert guard.check() is False
        finally:
            _request_var.reset(token)
            mgr._once_user_var.set(None)

    def test_once_refuses_two_factor_users(self):
        user = _user(password=hash_password("secret"), two_factor_enabled=True)
        guard = _guard_with(user)
        with _bound_request(_Session()):
            assert guard.once({"email": "a@b.com", "password": "secret"}) is False
            assert guard.check() is False
            assert guard.once({"email": "a@b.com", "password": "secret"}, allow_two_factor=True) is True
            assert guard.user() is user

    def test_once_still_works_without_two_factor(self):
        user = _user(password=hash_password("secret"))
        guard = _guard_with(user)
        session = _Session()
        with _bound_request(session):
            assert guard.once({"email": "a@b.com", "password": "bad"}) is False
            assert guard.once({"email": "a@b.com", "password": "secret"}) is True
            assert guard.user() is user
        assert session.data == {}  # nothing persisted

    def test_logout_drops_the_once_user(self):
        user = _user(id=5)
        guard = _guard_with(user, "api")
        with _bound_request(_Session()):
            guard.once_using_id(5)
            guard.logout()
            guard._model.find.return_value = None
            assert guard.check() is False


# ---------------------------------------------------------------------------
# AUTH-L4 / AUTH-L6 / AUTH-03: session handling around login and logout
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    def test_attempt_with_two_factor_reports_pending(self):
        user = _user(password=hash_password("secret"), two_factor_enabled=True)
        guard = _guard_with(user)
        auth = _AuthManager()
        auth._default_guard = guard
        session = _Session({"_2fa_attempts": 4})
        with _bound_request(session):
            assert auth.attempt({"email": "a@b.com", "password": "secret"}) is False
            assert auth.two_factor_pending() is True
            assert auth.check() is False
        assert session.data["_2fa_pending"] == 1
        assert "_2fa_attempts" not in session.data

    def test_bad_password_does_not_report_pending(self):
        user = _user(password=hash_password("secret"), two_factor_enabled=True)
        auth = _AuthManager()
        auth._default_guard = _guard_with(user)
        with _bound_request(_Session()):
            assert auth.attempt({"email": "a@b.com", "password": "nope"}) is False
            assert auth.two_factor_pending() is False

    def test_pending_challenge_expires(self):
        from hunt.auth.two_factor import PENDING_TTL

        guard = _SessionGuard("web")
        fresh = _Session({"_2fa_pending": 1, "_2fa_pending_at": int(time.time())})
        stale = _Session({"_2fa_pending": 1, "_2fa_pending_at": int(time.time()) - PENDING_TTL - 1})
        untimed = _Session({"_2fa_pending": 1})
        with _bound_request(fresh):
            assert guard.two_factor_pending() is True
        with _bound_request(stale):
            assert guard.two_factor_pending() is False
        with _bound_request(untimed):
            assert guard.two_factor_pending() is False

    def test_login_rotates_csrf_and_clears_two_factor_state(self):
        guard = _guard_with(_user())
        session = _Session(
            {"_csrf_token": "old", "_2fa_pending": 9, "_2fa_pending_at": 1, "_2fa_attempts": 3, "cart": [1]}
        )
        with _bound_request(session), patch("hunt.session.registry.SessionRegistry"):
            guard.login(_user())
        assert session.regenerated == 1
        assert "_csrf_token" not in session.data
        assert not [k for k in session.data if k.startswith("_2fa_")]
        assert session.data["_auth_id"] == 1
        assert session.data["cart"] == [1]

    def test_logout_flushes_session(self):
        guard = _guard_with(_user())
        session = _Session(
            {
                "_auth_id": 1,
                "_csrf_token": "tok",
                "_2fa_pending": 9,
                "_2fa_pending_secret": "enc",
                "private": "data",
                "_flash_new": {"status": "Bye"},
            }
        )
        with _bound_request(session), patch("hunt.session.registry.SessionRegistry"):
            guard.logout()
        assert session.data == {"_flash_new": {"status": "Bye"}}
        assert session.regenerated == 1

    def test_logout_of_one_guard_keeps_the_other_logged_in(self):
        admin = _guard_with(_user(), "admin")
        session = _Session({"_auth_id": 1, "_auth_id_admin": 2, "_csrf_token": "tok", "_2fa_pending": 9, "cart": [1]})
        with _bound_request(session), patch("hunt.session.registry.SessionRegistry"):
            admin.logout()
        assert session.data == {"_auth_id": 1, "cart": [1]}


class TestTokenGuardQuery:  # AUTH-L5
    def test_query_fallback_is_opt_in_through_config(self):
        auth = _AuthManager()
        model = MagicMock()
        auth.configure(
            {
                "api": {"driver": "token", "model": model},
                "legacy": {"driver": "token", "model": model, "allow_query": True},
            }
        )
        assert auth.guard("api")._allow_query is False
        assert auth.guard("legacy")._allow_query is True
        request = MagicMock()
        request.bearer_token.return_value = None
        request.query.return_value = "tok"
        with patch("hunt.auth.manager._get_current_request", return_value=request):
            assert auth.guard("api").user() is None
            model.where.assert_not_called()
            auth.guard("legacy").user()
            model.where.assert_called_once()


# ---------------------------------------------------------------------------
# AUTH-L1: password broker
# ---------------------------------------------------------------------------


class TestPasswordBrokerReal:
    def setup_method(self):
        from sqlalchemy import text

        with connection().connect() as conn:
            conn.execute(text("DELETE FROM fix_auth_reset_tokens"))
            conn.execute(text("DELETE FROM fix_auth_login_users"))
            conn.commit()
        self.user = FixLoginUser.create({"email": "reset@example.com", "password": hash_password("old-password")})
        self.broker = FixResetTokens()
        self.broker.set_model(FixLoginUser)

    def _rows(self) -> int:
        from sqlalchemy import text

        with connection().connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM fix_auth_reset_tokens")).fetchone()[0]

    def _age_tokens(self, seconds: int) -> None:
        from sqlalchemy import text

        with connection().connect() as conn:
            conn.execute(text("UPDATE fix_auth_reset_tokens SET created_at = created_at - :s"), {"s": seconds})
            conn.commit()

    def test_cooldown_keeps_the_first_link_valid(self):
        token = self.broker.send_reset_link("reset@example.com")
        assert token is not None
        assert self.broker.send_reset_link("reset@example.com") is None
        assert self.broker.token_valid("reset@example.com", token) is True
        assert self._rows() == 1
        self._age_tokens(self.broker.THROTTLE + 1)
        second = self.broker.send_reset_link("reset@example.com")
        assert second is not None
        assert self.broker.token_valid("reset@example.com", token) is False
        assert self.broker.token_valid("reset@example.com", second) is True

    def test_token_is_single_use(self):
        token = self.broker.send_reset_link("reset@example.com")
        creds = {"email": "reset@example.com", "token": token, "password": "brand-new-password"}
        with patch("hunt.session.registry.revoke_sessions_for") as revoke:
            assert self.broker.reset(creds) is True
            assert self.broker.reset({**creds, "password": "attacker-password"}) is False
        revoke.assert_called_once_with(self.user._attributes["id"])
        stored = FixLoginUser.find(self.user._attributes["id"])._attributes["password"]
        assert verify_password("brand-new-password", stored) is True
        assert self._rows() == 0

    def test_lost_race_does_not_change_the_password(self):
        token = self.broker.send_reset_link("reset@example.com")
        creds = {"email": "reset@example.com", "token": token, "password": "brand-new-password"}
        with patch.object(self.broker, "_consume", return_value=False):
            assert self.broker.reset(creds) is False
        stored = FixLoginUser.find(self.user._attributes["id"])._attributes["password"]
        assert verify_password("old-password", stored) is True

    @pytest.mark.parametrize("password", ["", "short", None, ["long-enough-list"]])
    def test_weak_password_is_refused_without_burning_the_token(self, password):
        token = self.broker.send_reset_link("reset@example.com")
        creds = {"email": "reset@example.com", "token": token, "password": password}
        assert self.broker.reset(creds) is False
        assert self.broker.token_valid("reset@example.com", token) is True

    def test_revoke_failure_is_logged_not_swallowed(self, caplog):
        token = self.broker.send_reset_link("reset@example.com")
        creds = {"email": "reset@example.com", "token": token, "password": "brand-new-password"}
        with (
            patch("hunt.session.registry.revoke_sessions_for", side_effect=RuntimeError("registry down")),
            caplog.at_level("ERROR", logger="hunt.auth"),
        ):
            assert self.broker.reset(creds) is True
        assert "could not be revoked" in caplog.text

    def test_unknown_email_returns_none(self):
        assert self.broker.send_reset_link("nobody@example.com") is None
        assert self.broker.create_token("nobody@example.com") is None
        assert self.broker.send_reset_link("") is None
        assert self.broker.send_reset_link(None) is None
        assert self._rows() == 0

    def test_create_token_returns_the_stored_address(self):
        assert self.broker.create_token("reset@example.com")[0] == "reset@example.com"

    def test_lookalike_address_gets_no_token(self):
        # Simulate a collation that matches a look-alike address to the real user.
        stored = MagicMock()
        stored._attributes = {"id": 1, "email": "reset@example.com"}
        with patch.object(self.broker, "_find_user", return_value=stored):
            assert self.broker.send_reset_link("resét@example.com") is None
            assert self._rows() == 0
            created = self.broker.create_token("resét@example.com")
        # create_token tells the caller where the token really belongs.
        assert created is not None
        assert created[0] == "reset@example.com"

    def test_ascii_case_difference_is_not_a_lookalike(self):
        assert PasswordBroker._same_address("Reset@Example.com", "reset@example.com") is True
        assert PasswordBroker._same_address("resét@example.com", "reset@example.com") is False


# ---------------------------------------------------------------------------
# AUTH-L2: email verification
# ---------------------------------------------------------------------------


class TestEmailVerificationPurpose:
    def setup_method(self):
        os.environ["APP_KEY"] = "a" * 32
        from hunt.auth.verification import _EmailVerification

        self.ev = _EmailVerification()

    @staticmethod
    def _params(url: str) -> dict:
        return dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))

    def test_signature_is_bound_to_the_email_verify_purpose(self):
        from hunt.security.signing import sign

        user = _user(id=1, email="a@b.com")
        params = self._params(self.ev.verification_url(user))
        payload = f"1:a@b.com:{params['expires']}"
        assert params["signature"] == sign(payload, purpose="email-verify")
        assert params["signature"] != sign(payload)
        # A signature made with the general-purpose key (e.g. a queue payload) is refused.
        assert self.ev.verify(user, params["expires"], sign(payload)) is False
        assert self.ev.verify(user, params["expires"], sign(payload, purpose="queue.v1")) is False
        user.save.assert_not_called()

    def test_expires_parameter_is_the_expiry_time(self):
        user = _user(id=1, email="a@b.com")
        before = int(time.time())
        expires = int(self._params(self.ev.verification_url(user))["expires"])
        assert before + self.ev.EXPIRY <= expires <= int(time.time()) + self.ev.EXPIRY

    def test_expired_and_far_future_links_are_refused(self):
        from hunt.security.signing import sign

        user = _user(id=1, email="a@b.com")
        for expires in (int(time.time()) - 5, int(time.time()) + self.ev.EXPIRY + 600):
            sig = sign(f"1:a@b.com:{expires}", purpose="email-verify")
            assert self.ev.verify(user, str(expires), sig) is False
        user.save.assert_not_called()

    def test_already_verified_user_is_not_written_again(self):
        user = _user(id=1, email="a@b.com", email_verified_at=123)
        params = self._params(self.ev.verification_url(user))
        assert self.ev.verify(user, params["expires"], params["signature"]) is True
        user.save.assert_not_called()
        assert user._attributes["email_verified_at"] == 123

    def test_bad_signature_is_refused_even_when_already_verified(self):
        user = _user(id=1, email="a@b.com", email_verified_at=123)
        params = self._params(self.ev.verification_url(user))
        assert self.ev.verify(user, params["expires"], "0" * 64) is False
        assert self.ev.verify(user, params["expires"], None) is False


class TestRegistryFailuresAreLogged:
    def test_login_succeeds_but_logs_when_registry_fails(self, caplog):
        guard = _guard_with(_user())
        session = _Session()
        with (
            _bound_request(session),
            patch("hunt.session.registry.SessionRegistry") as registry,
            caplog.at_level("ERROR", logger="hunt.auth"),
        ):
            registry.return_value.register.side_effect = RuntimeError("db down")
            guard.login(_user())
        assert session.data["_auth_id"] == 1
        assert "session registry" in caplog.text

    def test_logout_completes_but_logs_when_registry_fails(self, caplog):
        guard = _guard_with(_user())
        session = _Session({"_auth_id": 1})
        with (
            _bound_request(session),
            patch("hunt.session.registry.SessionRegistry") as registry,
            caplog.at_level("ERROR", logger="hunt.auth"),
        ):
            registry.return_value.deregister.side_effect = RuntimeError("db down")
            guard.logout()
        assert session.data == {}
        assert "session registry" in caplog.text
