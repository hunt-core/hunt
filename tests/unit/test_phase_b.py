"""Phase B tests: Gates & Policies, Password Reset, Email Verification, Multi-Guard Auth."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(id_=1, email="user@example.com", password="hashed", **extra):
    user = MagicMock()
    attrs = {"id": id_, "email": email, "password": password, **extra}
    user._attributes = attrs
    user.__class__.__name__ = "User"
    return user


# ===========================================================================
# Gate & Policies
# ===========================================================================


class TestGateDefineAndCheck:
    def setup_method(self):
        from hunt.auth.gate import _Gate

        self.gate = _Gate()

    def test_allows_defined_ability(self):
        self.gate.define("view-dashboard", lambda user: True)
        user = _make_user()
        assert self.gate.for_user(user).allows("view-dashboard") is True

    def test_denies_undefined_ability(self):
        user = _make_user()
        assert self.gate.for_user(user).allows("fly") is False

    def test_denies_guest_by_default(self):
        self.gate.define("view-dashboard", lambda user: True)
        # user=None means guest
        assert self.gate._check("view-dashboard", None) is False

    def test_ability_receives_args(self):
        def edit_post(user, post):
            return post.owner_id == user._attributes["id"]

        self.gate.define("edit-post", edit_post)
        user = _make_user(id_=42)
        post = MagicMock()
        post.owner_id = 42
        assert self.gate.for_user(user).allows("edit-post", post) is True
        post.owner_id = 99
        assert self.gate.for_user(user).allows("edit-post", post) is False

    def test_denies_is_inverse(self):
        self.gate.define("write", lambda user: False)
        user = _make_user()
        assert self.gate.for_user(user).denies("write") is True

    def test_any_returns_true_if_one_passes(self):
        self.gate.define("a", lambda user: False)
        self.gate.define("b", lambda user: True)
        user = _make_user()
        assert self.gate.for_user(user).any(["a", "b"]) is True

    def test_none_returns_true_if_all_fail(self):
        self.gate.define("a", lambda user: False)
        self.gate.define("b", lambda user: False)
        user = _make_user()
        assert self.gate.for_user(user).none(["a", "b"]) is True

    def test_can_alias(self):
        self.gate.define("read", lambda user: True)
        with patch("hunt.auth.gate._Gate._current_user", return_value=_make_user()):
            assert self.gate.can("read") is True

    def test_authorize_raises_on_deny(self):
        self.gate.define("admin-only", lambda user: False)
        user = _make_user()
        from hunt.http.response import HttpException

        with pytest.raises(HttpException) as exc_info:
            self.gate.for_user(user).authorize("admin-only")
        assert exc_info.value.status == 403

    def test_authorize_passes_on_allow(self):
        self.gate.define("open", lambda user: True)
        user = _make_user()
        self.gate.for_user(user).authorize("open")  # no exception


class TestGateBeforeAfterCallbacks:
    def setup_method(self):
        from hunt.auth.gate import _Gate

        self.gate = _Gate()

    def test_before_callback_can_grant(self):
        self.gate.define("whatever", lambda user: False)
        self.gate.before(lambda user, ability: True)  # always grant
        user = _make_user()
        assert self.gate.for_user(user).allows("whatever") is True

    def test_before_callback_can_deny(self):
        self.gate.define("whatever", lambda user: True)
        self.gate.before(lambda user, ability: False)  # always deny
        user = _make_user()
        assert self.gate.for_user(user).allows("whatever") is False

    def test_before_returning_none_does_not_short_circuit(self):
        self.gate.define("whatever", lambda user: True)
        self.gate.before(lambda user, ability: None)
        user = _make_user()
        assert self.gate.for_user(user).allows("whatever") is True

    def test_after_callback_called(self):
        called = []
        self.gate.define("act", lambda user: True)
        self.gate.after(lambda user, ability, result: called.append(result))
        user = _make_user()
        self.gate.for_user(user).allows("act")
        assert called == [True]


class TestGatePolicies:
    def setup_method(self):
        from hunt.auth.gate import _Gate

        self.gate = _Gate()

    def test_policy_method_invoked(self):
        class PostPolicy:
            def view(self, user, post):
                return post.public

        class Post:
            def __init__(self, public):
                self.public = public

        self.gate.policy(Post, PostPolicy)
        user = _make_user()
        assert self.gate.for_user(user).allows("view", Post(True)) is True
        assert self.gate.for_user(user).allows("view", Post(False)) is False

    def test_policy_before_short_circuits(self):
        class SuperPolicy:
            def before(self, user, ability):
                return True  # always allow

            def view(self, user, post):
                return False

        class Doc:
            pass

        self.gate.policy(Doc, SuperPolicy)
        user = _make_user()
        assert self.gate.for_user(user).allows("view", Doc()) is True

    def test_policy_missing_method_returns_false(self):
        class EmptyPolicy:
            pass

        class Item:
            pass

        self.gate.policy(Item, EmptyPolicy)
        user = _make_user()
        assert self.gate.for_user(user).allows("delete", Item()) is False


# ===========================================================================
# Password Reset
# ===========================================================================

_BROKER_APP_KEY = "b" * 32


class TestPasswordBroker:
    def setup_method(self):
        os.environ["APP_KEY"] = _BROKER_APP_KEY
        from hunt.auth.passwords import PasswordBroker

        self.broker = PasswordBroker()

        self.user = _make_user()
        model = MagicMock()
        model.where.return_value.first.return_value = self.user
        self.broker.set_model(model)

    def teardown_method(self):
        os.environ.pop("APP_KEY", None)

    def test_send_reset_link_returns_token(self):
        with patch.object(self.broker, "_delete_existing"), patch.object(self.broker, "_insert_token"):
            token = self.broker.send_reset_link("user@example.com")
        assert token is not None
        assert len(token) == 64  # 32 bytes → 64 hex chars

    def test_send_reset_link_returns_none_for_unknown_email(self):
        self.broker._model.where.return_value.first.return_value = None
        with patch.object(self.broker, "_delete_existing"), patch.object(self.broker, "_insert_token"):
            result = self.broker.send_reset_link("nope@example.com")
        assert result is None

    def test_token_valid_matches_hashed(self):
        raw = "abc123"
        hashed = self.broker._hash(raw)
        now = int(time.time())
        row = {"email": "user@example.com", "token": hashed, "created_at": now}
        with patch.object(self.broker, "_get_row", return_value=row):
            assert self.broker.token_valid("user@example.com", raw) is True

    def test_token_valid_fails_wrong_token(self):
        row = {"email": "user@example.com", "token": self.broker._hash("right"), "created_at": int(time.time())}
        with patch.object(self.broker, "_get_row", return_value=row):
            assert self.broker.token_valid("user@example.com", "wrong") is False

    def test_token_valid_fails_expired(self):
        hashed = self.broker._hash("tok")
        old_ts = int(time.time()) - 7200  # 2 hours ago
        row = {"email": "user@example.com", "token": hashed, "created_at": old_ts}
        with patch.object(self.broker, "_get_row", return_value=row):
            assert self.broker.token_valid("user@example.com", "tok") is False

    def test_token_valid_no_row(self):
        with patch.object(self.broker, "_get_row", return_value=None):
            assert self.broker.token_valid("x@example.com", "tok") is False

    def test_reset_updates_password(self):
        raw = "newpass"
        hashed_token = self.broker._hash(raw)
        row = {"email": "user@example.com", "token": hashed_token, "created_at": int(time.time())}
        with (
            patch.object(self.broker, "_get_row", return_value=row),
            patch.object(self.broker, "_delete_existing"),
            patch("hunt.auth.manager.hash_password", return_value="newhash"),
        ):
            result = self.broker.reset({"email": "user@example.com", "token": raw, "password": "newpass"})
        assert result is True
        assert self.user._attributes["password"] == "newhash"

    def test_reset_fails_bad_token(self):
        row = {"email": "user@example.com", "token": self.broker._hash("good"), "created_at": int(time.time())}
        with patch.object(self.broker, "_get_row", return_value=row):
            result = self.broker.reset({"email": "user@example.com", "token": "bad", "password": "x"})
        assert result is False


# ===========================================================================
# Email Verification
# ===========================================================================

_TEST_APP_KEY = "a" * 32  # 32-char key meets the minimum length requirement


def _url_params(url: str) -> dict:
    qs = url.split("?", 1)[1]
    return dict(p.split("=", 1) for p in qs.split("&"))


class TestEmailVerification:
    def setup_method(self):
        os.environ["APP_KEY"] = _TEST_APP_KEY
        from hunt.auth.verification import _EmailVerification

        self.ev = _EmailVerification()

    def test_verification_url_contains_id_expires_signature(self):
        user = _make_user(id_=1, email="a@b.com")
        url = self.ev.verification_url(user, base_url="http://example.com")
        assert "/email/verify" in url
        assert "id=" in url
        assert "expires=" in url
        assert "signature=" in url
        # Email must NOT appear in the URL (PII protection)
        assert "a@b.com" not in url

    def test_verify_succeeds_with_valid_token(self):
        user = _make_user(id_=1, email="a@b.com")
        url = self.ev.verification_url(user, base_url="http://example.com")
        params = _url_params(url)
        expires = params["expires"]
        sig = params["signature"]

        user.save = MagicMock()
        assert self.ev.verify(user, expires, sig) is True
        assert user._attributes.get("email_verified_at") is not None

    def test_verify_fails_bad_signature(self):
        user = _make_user(id_=1, email="a@b.com")
        url = self.ev.verification_url(user, base_url="")
        params = _url_params(url)
        expires = params["expires"]

        user.save = MagicMock()
        assert self.ev.verify(user, expires, "badsig") is False

    def test_verify_fails_expired_token(self):
        user = _make_user(id_=1, email="a@b.com")
        old_ts = int(time.time()) - 7200
        from hunt.security.signing import sign

        sig = sign(f"1:a@b.com:{old_ts}")
        user.save = MagicMock()
        assert self.ev.verify(user, str(old_ts), sig) is False

    def test_verify_fails_wrong_user(self):
        user = _make_user(id_=1, email="a@b.com")
        url = self.ev.verification_url(user, base_url="")
        params = _url_params(url)
        expires = params["expires"]
        sig = params["signature"]

        other_user = _make_user(id_=99, email="a@b.com")
        other_user.save = MagicMock()
        assert self.ev.verify(other_user, expires, sig) is False

    def test_verify_fails_wrong_email(self):
        user = _make_user(id_=1, email="a@b.com")
        url = self.ev.verification_url(user, base_url="")
        params = _url_params(url)
        expires = params["expires"]
        sig = params["signature"]

        other_user = _make_user(id_=1, email="other@b.com")
        other_user.save = MagicMock()
        assert self.ev.verify(other_user, expires, sig) is False

    def test_is_verified_true_when_timestamp_set(self):
        user = _make_user(email_verified_at=int(time.time()))
        assert self.ev.is_verified(user) is True

    def test_is_verified_false_when_not_set(self):
        user = _make_user()
        assert self.ev.is_verified(user) is False

    def test_resend_returns_fresh_url(self):
        user = _make_user(id_=1, email="a@b.com")
        url = self.ev.resend(user, base_url="http://example.com")
        assert "/email/verify" in url


# ===========================================================================
# Multi-Guard Auth
# ===========================================================================


class TestSessionGuard:
    def setup_method(self):
        from hunt.auth.manager import _SessionGuard

        self.guard = _SessionGuard("web")
        model = MagicMock()
        self.user = _make_user()
        model.find.return_value = self.user
        self.guard.set_model(model)

        self.session = MagicMock()
        self.session.get.return_value = 1

    _SENTINEL = object()

    def _patch_session(self, session=_SENTINEL):
        from hunt.auth import manager as mgr

        value = self.session if session is self._SENTINEL else session
        return patch.object(mgr, "_get_session", return_value=value)

    def test_check_true_when_id_in_session(self):
        with self._patch_session():
            assert self.guard.check() is True

    def test_check_false_when_no_session(self):
        with self._patch_session(None):
            assert self.guard.check() is False

    def test_user_fetched_from_model(self):
        with self._patch_session():
            u = self.guard.user()
        assert u is self.user

    def test_id_returns_session_value(self):
        with self._patch_session():
            assert self.guard.id() == 1

    def test_guest_is_inverse_of_check(self):
        with self._patch_session():
            assert self.guard.guest() is False

    def test_login_stores_user_id_in_session(self):
        self.session.get.return_value = None
        with self._patch_session():
            self.guard.login(self.user)
        self.session.put.assert_called_once_with("_auth_id", 1)
        self.session.regenerate.assert_called()

    def test_logout_forgets_key(self):
        with self._patch_session():
            self.guard.logout()
        self.session.forget.assert_called_once_with("_auth_id")

    def test_session_key_web(self):
        from hunt.auth.manager import _SessionGuard

        g = _SessionGuard("web")
        assert g._session_key == "_auth_id"

    def test_session_key_named(self):
        from hunt.auth.manager import _SessionGuard

        g = _SessionGuard("admin")
        assert g._session_key == "_auth_id_admin"

    def test_attempt_succeeds(self):
        model = MagicMock()
        model.where.return_value.first.return_value = self.user

        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web", model)

        with self._patch_session():
            with patch("hunt.auth.manager.verify_password", return_value=True):
                result = guard.attempt({"email": "user@example.com", "password": "plain"})
        assert result is True

    def test_attempt_fails_wrong_password(self):
        model = MagicMock()
        model.where.return_value.first.return_value = self.user

        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web", model)

        with self._patch_session():
            with patch("hunt.auth.manager.verify_password", return_value=False):
                result = guard.attempt({"email": "user@example.com", "password": "bad"})
        assert result is False

    def test_attempt_fails_no_user(self):
        model = MagicMock()
        model.where.return_value.first.return_value = None

        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web", model)

        with self._patch_session():
            result = guard.attempt({"email": "nope@example.com", "password": "x"})
        assert result is False


class TestTokenGuard:
    def setup_method(self):
        from hunt.auth.manager import _TokenGuard

        self.model = MagicMock()
        self.user = _make_user()
        self.model.where.return_value.first.return_value = self.user
        self.guard = _TokenGuard("api", self.model, "api_token")

    def _mock_request(self, bearer=None, query_token=None):
        req = MagicMock()
        req.bearer_token.return_value = bearer
        req.query.return_value = query_token
        return req

    def test_user_found_via_bearer(self):
        import hashlib

        req = self._mock_request(bearer="token123")
        with patch("hunt.auth.manager._get_current_request", return_value=req):
            u = self.guard.user()
        assert u is self.user
        # Token is hashed before DB lookup to avoid plaintext storage
        expected_hash = hashlib.sha256(b"token123").hexdigest()
        self.model.where.assert_called_with("api_token", expected_hash)

    def test_user_found_via_query_param(self):
        req = self._mock_request(bearer=None, query_token="qtoken")
        with patch("hunt.auth.manager._get_current_request", return_value=req):
            u = self.guard.user()
        assert u is self.user

    def test_check_true_when_user_found(self):
        req = self._mock_request(bearer="tok")
        with patch("hunt.auth.manager._get_current_request", return_value=req):
            assert self.guard.check() is True

    def test_check_false_when_no_request(self):
        with patch("hunt.auth.manager._get_current_request", return_value=None):
            assert self.guard.check() is False

    def test_attempt_raises(self):
        with pytest.raises(NotImplementedError):
            self.guard.attempt({})

    def test_login_raises(self):
        with pytest.raises(NotImplementedError):
            self.guard.login(MagicMock())

    def test_logout_raises(self):
        with pytest.raises(NotImplementedError):
            self.guard.logout()


class TestAuthManager:
    def setup_method(self):
        from hunt.auth.manager import _AuthManager

        self.auth = _AuthManager()

    def test_default_guard_is_session(self):
        from hunt.auth.manager import _SessionGuard

        assert isinstance(self.auth._default_guard, _SessionGuard)

    def test_configure_creates_guards(self):
        model = MagicMock()
        self.auth.configure({"web": {"driver": "session", "model": model}})
        from hunt.auth.manager import _SessionGuard

        assert isinstance(self.auth._guards["web"], _SessionGuard)

    def test_configure_token_guard(self):
        model = MagicMock()
        self.auth.configure({"api": {"driver": "token", "model": model, "field": "api_token"}})
        from hunt.auth.manager import _TokenGuard

        assert isinstance(self.auth._guards["api"], _TokenGuard)

    def test_configure_unknown_driver_raises(self):
        with pytest.raises(ValueError, match="Unknown guard driver"):
            self.auth.configure({"bad": {"driver": "magic"}})

    def test_guard_by_name(self):
        model = MagicMock()
        self.auth.configure({"api": {"driver": "token", "model": model}})
        g = self.auth.guard("api")
        from hunt.auth.manager import _TokenGuard

        assert isinstance(g, _TokenGuard)

    def test_guard_lazy_creates_session_guard(self):
        g = self.auth.guard("unknown")
        from hunt.auth.manager import _SessionGuard

        assert isinstance(g, _SessionGuard)

    def test_web_configure_updates_default(self):
        model = MagicMock()
        self.auth.configure({"web": {"driver": "session", "model": model}})
        assert self.auth._default_guard is self.auth._guards["web"]

    def test_proxy_methods_delegate_to_default_guard(self):
        mock_guard = MagicMock()
        mock_guard.user.return_value = _make_user()
        mock_guard.check.return_value = True
        mock_guard.guest.return_value = False
        mock_guard.id.return_value = 1
        self.auth._default_guard = mock_guard

        assert self.auth.user() is mock_guard.user.return_value
        assert self.auth.check() is True
        assert self.auth.guest() is False
        assert self.auth.id() == 1


# ===========================================================================
# EnsureEmailIsVerified middleware
# ===========================================================================


class TestEnsureEmailIsVerified:
    @pytest.mark.asyncio
    async def test_passes_verified_user(self):
        from hunt.http.middleware.verified import EnsureEmailIsVerified

        mw = EnsureEmailIsVerified()

        user = _make_user(email_verified_at=int(time.time()))
        request = MagicMock()
        response = MagicMock()
        next_handler = MagicMock(return_value=response)

        async def async_next(req):
            return next_handler(req)

        with patch("hunt.auth.manager.Auth") as mock_auth, patch("hunt.auth.verification.EmailVerification") as mock_ev:
            mock_auth.user.return_value = user
            mock_ev.is_verified.return_value = True
            result = await mw.handle(request, async_next)

        assert result is response

    @pytest.mark.asyncio
    async def test_redirects_unverified_user(self):
        from hunt.http.middleware.verified import EnsureEmailIsVerified
        from hunt.http.response import RedirectResponse

        mw = EnsureEmailIsVerified()

        user = _make_user()
        request = MagicMock()

        async def async_next(req):
            return MagicMock()

        with patch("hunt.auth.manager.Auth") as mock_auth, patch("hunt.auth.verification.EmailVerification") as mock_ev:
            mock_auth.user.return_value = user
            mock_ev.is_verified.return_value = False
            result = await mw.handle(request, async_next)

        assert isinstance(result, RedirectResponse)

    @pytest.mark.asyncio
    async def test_redirects_guest(self):
        from hunt.http.middleware.verified import EnsureEmailIsVerified
        from hunt.http.response import RedirectResponse

        mw = EnsureEmailIsVerified()
        request = MagicMock()

        async def async_next(req):
            return MagicMock()

        with patch("hunt.auth.manager.Auth") as mock_auth, patch("hunt.auth.verification.EmailVerification") as mock_ev:
            mock_auth.user.return_value = None
            mock_ev.is_verified.return_value = False
            result = await mw.handle(request, async_next)

        assert isinstance(result, RedirectResponse)


# ===========================================================================
# Controller.authorize()
# ===========================================================================


class TestControllerAuthorize:
    def test_authorize_delegates_to_gate(self):
        from hunt.http.controller import Controller

        ctrl = Controller()

        with patch("hunt.auth.gate.Gate") as mock_gate:
            ctrl.authorize("edit-post", MagicMock())
        mock_gate.authorize.assert_called_once()

    def test_authorize_propagates_exception(self):
        from hunt.http.controller import Controller
        from hunt.http.response import HttpException

        ctrl = Controller()

        with patch("hunt.auth.gate.Gate") as mock_gate:
            mock_gate.authorize.side_effect = HttpException(403, "Unauthorized")
            with pytest.raises(HttpException):
                ctrl.authorize("edit-post")


# ===========================================================================
# View directives: @can / @cannot
# ===========================================================================


class TestCanDirectives:
    def test_can_directive_renders(self):
        from hunt.view.directives import preprocess

        source = "@can('edit-post')\n<button>Edit</button>\n@endcan"
        out = preprocess(source)
        assert "{% if can('edit-post') %}" in out
        assert "{% endif %}" in out

    def test_cannot_directive_renders(self):
        from hunt.view.directives import preprocess

        source = "@cannot('delete')\n<p>Not allowed</p>\n@endcannot"
        out = preprocess(source)
        assert "{% if not can('delete') %}" in out
        assert "{% endif %}" in out

    def test_can_with_model_arg(self):
        from hunt.view.directives import preprocess

        source = "@can('view', post)\n<div>Post</div>\n@endcan"
        out = preprocess(source)
        assert "{% if can('view', post) %}" in out


# ===========================================================================
# auth __init__ exports
# ===========================================================================


class TestAuthPackageExports:
    def test_exports_auth(self):
        from hunt.auth import Auth

        assert Auth is not None

    def test_exports_gate(self):
        from hunt.auth import Gate

        assert Gate is not None

    def test_exports_password(self):
        from hunt.auth import Password

        assert Password is not None

    def test_exports_email_verification(self):
        from hunt.auth import EmailVerification

        assert EmailVerification is not None
