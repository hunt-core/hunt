"""Regression tests for AUTH-01 (gate), AUTH-02/03 (TOTP replay and lockout), AUTH-L3 and AUTH-L7."""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pyotp
import pytest

os.environ.setdefault("DB_CONNECTION", "sqlite")
os.environ.setdefault("DB_DATABASE", ":memory:")

import hunt.auth.two_factor as tf_mod
from hunt.auth.controllers import two_factor as ctl
from hunt.auth.gate import _Gate
from hunt.auth.two_factor import TwoFactor
from hunt.database.model import Model
from hunt.database.schema.builder import Schema


class _User:
    def __init__(self, id_: int = 1) -> None:
        self._attributes = {"id": id_}


# ---------------------------------------------------------------------------
# AUTH-01
# ---------------------------------------------------------------------------


class TestGateAsync:
    def setup_method(self):
        self.gate = _Gate()
        self.user = _User()

    def test_async_ability_is_rejected_at_registration(self):
        async def can_edit(user):
            return False

        with pytest.raises(TypeError, match="synchronous"):
            self.gate.define("edit", can_edit)
        assert self.gate.for_user(self.user).allows("edit") is False

    def test_async_partial_and_callable_object_are_rejected(self):
        async def can_edit(user, flag):
            return False

        class AsyncCallable:
            async def __call__(self, user):
                return False

        with pytest.raises(TypeError):
            self.gate.define("edit", functools.partial(can_edit, flag=True))
        with pytest.raises(TypeError):
            self.gate.define("edit", AsyncCallable())

    def test_async_before_and_after_hooks_are_rejected(self):
        async def hook(user, ability, result=None):
            return None

        with pytest.raises(TypeError):
            self.gate.before(hook)
        with pytest.raises(TypeError):
            self.gate.after(hook)
        # Nothing was registered: guests are still denied.
        assert self.gate.for_user(None).allows("anything") is False

    def test_async_policy_method_is_rejected_at_registration(self):
        class Post:
            pass

        class PostPolicy:
            async def update(self, user, post):
                return False

        with pytest.raises(TypeError, match=r"PostPolicy\.update"):
            self.gate.policy(Post, PostPolicy)

    def test_awaitable_result_fails_closed_at_check_time(self):
        async def coro(*_a):
            return False

        # Sync callables that hand back a coroutine get past registration.
        self.gate.define("edit", lambda user: coro())
        assert self.gate.for_user(self.user).allows("edit") is False

    def test_awaitable_before_hook_denies_everyone_including_guests(self):
        async def coro():
            return None

        self.gate.define("edit", lambda user: True)
        self.gate.before(lambda user, ability: coro())
        assert self.gate.for_user(None).allows("edit") is False
        assert self.gate.for_user(self.user).allows("edit") is False

    def test_awaitable_policy_results_fail_closed(self):
        async def coro():
            return False

        class Post:
            pass

        class PostPolicy:
            def update(self, user, post):
                return coro()

        class HookPolicy:
            def before(self, user, ability):
                return coro()

            def update(self, user, post):
                return True

        self.gate.policy(Post, PostPolicy)
        assert self.gate.for_user(self.user).allows("update", Post()) is False
        self.gate.policy(Post, HookPolicy)
        assert self.gate.for_user(self.user).allows("update", Post()) is False

    def test_sync_callbacks_still_work(self):
        self.gate.define("yes", lambda user: True)
        self.gate.define("no", lambda user: False)
        assert self.gate.for_user(self.user).allows("yes") is True
        assert self.gate.for_user(self.user).allows("no") is False


class TestGatePolicyLookup:
    def setup_method(self):
        self.gate = _Gate()
        self.user = _User()

    def test_subclass_of_protected_model_uses_parent_policy(self):
        class Post:
            pass

        class DraftPost(Post):
            pass

        class PostPolicy:
            def update(self, user, post):
                return False

        self.gate.policy(Post, PostPolicy)
        # Without MRO lookup this fell through to the permissive ability callback.
        self.gate.define("update", lambda user, post: True)
        assert self.gate.for_user(self.user).allows("update", DraftPost()) is False
        assert self.gate.for_user(self.user).allows("update", DraftPost) is False

    def test_most_specific_policy_wins(self):
        class Post:
            pass

        class DraftPost(Post):
            pass

        class PostPolicy:
            def view(self, user, post):
                return False

        class DraftPolicy:
            def view(self, user, post):
                return True

        self.gate.policy(Post, PostPolicy).policy(DraftPost, DraftPolicy)
        assert self.gate.for_user(self.user).allows("view", DraftPost()) is True
        assert self.gate.for_user(self.user).allows("view", Post()) is False

    @pytest.mark.parametrize("ability", ["before", "_secret", "__init__", "__class__", "helper.method", ""])
    def test_only_public_policy_methods_are_abilities(self, ability):
        class Post:
            pass

        class PostPolicy:
            def before(self, user, ability):
                return None

            def _secret(self, user, post):
                return True

        self.gate.policy(Post, PostPolicy)
        assert self.gate.for_user(self.user).allows(ability, Post()) is False

    def test_non_callable_policy_attribute_is_not_an_ability(self):
        class Post:
            pass

        class PostPolicy:
            publish = True

        self.gate.policy(Post, PostPolicy)
        assert self.gate.for_user(self.user).allows("publish", Post()) is False


# ---------------------------------------------------------------------------
# AUTH-02: canonical codes and atomic single-use consume
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _memory_stores():
    """Run every test against the in-process stores (no Redis) and start clean."""
    with tf_mod._used_codes_lock:
        tf_mod._used_codes.clear()
        tf_mod._last_steps.clear()
    with tf_mod._failures_lock:
        tf_mod._failures.clear()
    with patch.object(tf_mod, "_redis", return_value=None):
        yield


class TestTotpConsume:
    def test_normalize_code(self):
        assert TwoFactor.normalize_code("123 456") == "123456"
        assert TwoFactor.normalize_code(" 123456 ") == "123456"
        for bad in (
            "12345",
            "1234567",
            "12345a",
            "123\t456",
            "\uff11\uff12\uff13\uff14\uff15\uff16",
            "",
            None,
            123456,
            ["123456"],
        ):
            assert TwoFactor.normalize_code(bad) is None

    def test_verify_step_returns_same_step_for_any_spelling(self):
        secret = TwoFactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        step = TwoFactor.verify_step(secret, code)
        assert step is not None
        assert TwoFactor.verify_step(secret, f"{code[:3]} {code[3:]}") == step
        assert TwoFactor.verify_step(secret, f" {code} ") == step
        assert TwoFactor.verify(secret, code) is True

    def test_verify_step_rejects_wrong_and_malformed_codes(self):
        secret = TwoFactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        wrong = f"{(int(code) + 1) % 1000000:06d}"
        assert TwoFactor.verify_step(secret, wrong) is None
        assert TwoFactor.verify_step(secret, code + "0") is None
        assert TwoFactor.verify_step(secret, None) is None
        assert TwoFactor.verify_step("", code) is None

    def test_adjacent_steps_are_accepted_with_their_own_counter(self):
        secret = TwoFactor.generate_secret()
        totp = pyotp.TOTP(secret)
        now = int(time.time()) // 30
        assert TwoFactor.verify_step(secret, totp.generate_otp(now - 1)) == now - 1
        assert TwoFactor.verify_step(secret, totp.generate_otp(now + 1)) == now + 1

    def test_consume_is_single_use(self):
        assert TwoFactor.consume(1, 100) is True
        assert TwoFactor.consume(1, 100) is False

    def test_consume_rejects_older_steps_and_accepts_newer(self):
        assert TwoFactor.consume(1, 100) is True
        assert TwoFactor.consume(1, 99) is False
        assert TwoFactor.consume(1, 101) is True

    def test_consume_is_per_user(self):
        assert TwoFactor.consume(1, 100) is True
        assert TwoFactor.consume(2, 100) is True

    def test_concurrent_consume_succeeds_exactly_once(self):
        results: list[bool] = []
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            results.append(TwoFactor.consume(7, 555))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1
        assert results.count(False) == 15

    def test_consume_uses_one_atomic_redis_call(self):
        client = MagicMock()
        client.eval.return_value = 1
        with patch.object(tf_mod, "_redis", return_value=client):
            assert TwoFactor.consume(42, 900) is True
            client.eval.return_value = 0
            assert TwoFactor.consume(42, 900) is False
        assert client.eval.call_count == 2
        args = client.eval.call_args.args
        assert args[1:] == (1, "hunt:2fa:step:42", 900, 90)
        client.get.assert_not_called()

    def test_consume_falls_back_to_memory_when_redis_fails(self):
        client = MagicMock()
        client.eval.side_effect = ConnectionError("down")
        with patch.object(tf_mod, "_redis", return_value=client):
            assert TwoFactor.consume(9, 10) is True
            assert TwoFactor.consume(9, 10) is False

    def test_legacy_mark_used_is_canonical(self):
        TwoFactor.mark_used(1, "123456")
        assert TwoFactor.is_used(1, "123 456") is True


# ---------------------------------------------------------------------------
# AUTH-03: per-user lockout
# ---------------------------------------------------------------------------


class TestRegisterAttempt:
    def test_first_attempts_are_allowed_then_locked(self):
        for _ in range(tf_mod.MAX_FAILURES):
            assert TwoFactor.register_attempt(3) is True
        assert TwoFactor.register_attempt(3) is False
        assert TwoFactor.lockout_remaining(3) > 0
        assert TwoFactor.register_attempt(4) is True  # other users are unaffected

    def test_success_clears_the_counter(self):
        for _ in range(tf_mod.MAX_FAILURES - 1):
            assert TwoFactor.register_attempt(3) is True
        TwoFactor.clear_failures(3)
        for _ in range(tf_mod.MAX_FAILURES):
            assert TwoFactor.register_attempt(3) is True

    def test_lockout_grows_exponentially(self):
        assert tf_mod._lockout_for(tf_mod.MAX_FAILURES - 1) == 0
        assert tf_mod._lockout_for(tf_mod.MAX_FAILURES) == tf_mod.LOCKOUT_BASE
        assert tf_mod._lockout_for(tf_mod.MAX_FAILURES + 1) == tf_mod.LOCKOUT_BASE * 2
        assert tf_mod._lockout_for(tf_mod.MAX_FAILURES + 2) == tf_mod.LOCKOUT_BASE * 4
        assert tf_mod._lockout_for(tf_mod.MAX_FAILURES + 500) == tf_mod.LOCKOUT_MAX

    def test_one_attempt_per_lockout_period_after_the_limit(self):
        clock = [1_000_000.0]
        with patch.object(tf_mod.time, "time", side_effect=lambda: clock[0]):
            for _ in range(tf_mod.MAX_FAILURES):
                assert TwoFactor.register_attempt(3) is True
            assert TwoFactor.register_attempt(3) is False
            clock[0] += tf_mod.LOCKOUT_BASE + 1
            assert TwoFactor.register_attempt(3) is True
            assert TwoFactor.register_attempt(3) is False
            clock[0] += tf_mod.LOCKOUT_BASE + 1  # the second lockout is twice as long
            assert TwoFactor.register_attempt(3) is False
            clock[0] += tf_mod.LOCKOUT_BASE
            assert TwoFactor.register_attempt(3) is True

    def test_concurrent_attempts_cannot_exceed_the_limit(self):
        results: list[bool] = []
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            results.append(TwoFactor.register_attempt(11))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == tf_mod.MAX_FAILURES

    def test_redis_counter_is_atomic(self):
        client = MagicMock()
        client.exists.return_value = 0
        client.incr.side_effect = [1, tf_mod.MAX_FAILURES, tf_mod.MAX_FAILURES + 1, tf_mod.MAX_FAILURES + 2]
        client.set.side_effect = [True, True, None]
        with patch.object(tf_mod, "_redis", return_value=client):
            assert TwoFactor.register_attempt(5) is True  # 1st failure, no lock
            assert TwoFactor.register_attempt(5) is True  # reaches the limit, lock set
            assert TwoFactor.register_attempt(5) is True  # won the NX lock
            assert TwoFactor.register_attempt(5) is False  # lost the NX lock
        client.incr.assert_called_with("hunt:2fa:fail:5")
        last = client.set.call_args
        assert last.args[0] == "hunt:2fa:lock:5"
        assert last.kwargs["nx"] is True


class _Session:
    def __init__(self, data: dict | None = None) -> None:
        self.data = dict(data or {})
        self.flashed: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def put(self, key, value):
        self.data[key] = value

    def forget(self, key):
        self.data.pop(key, None)

    def flash(self, key, value):
        self.flashed[key] = value

    def all(self):
        return dict(self.data)

    def flush(self):
        self.data = {}

    def regenerate(self):
        return "new"


def _request(session: _Session, code: str = "", path: str = "/two-factor/challenge") -> MagicMock:
    request = MagicMock()
    request.session.return_value = session
    request.input.side_effect = lambda key, default=None: {"code": code}.get(key, default)
    request.path = path
    return request


def _fresh_pending(user_id: int = 10) -> _Session:
    return _Session({"_2fa_pending": user_id, "_2fa_pending_at": int(time.time())})


class TestChallengeController:
    def setup_method(self):
        self.secret = TwoFactor.generate_secret()
        self.user = MagicMock()
        self.user._attributes = {"id": 10, "two_factor_secret": "enc", "two_factor_recovery_codes": "[]"}
        self.guard = MagicMock()
        self.guard._model.find.return_value = self.user

    def _post(self, session: _Session, code: str):
        with (
            patch.object(ctl, "Auth") as auth,
            patch.object(ctl.TwoFactor, "decrypt_secret", return_value=self.secret),
        ):
            auth._default_guard = self.guard
            return ctl.TwoFactorChallengeController().store(_request(session, code))

    def _wrong(self) -> str:
        valid = {pyotp.TOTP(self.secret).generate_otp(int(time.time()) // 30 + o) for o in (-2, -1, 0, 1, 2)}
        return next(c for c in (f"{n:06d}" for n in range(1000)) if c not in valid)

    def test_valid_code_logs_in(self):
        self._post(_fresh_pending(), pyotp.TOTP(self.secret).now())
        self.guard.login.assert_called_once_with(self.user)

    def test_spaced_variant_is_rejected_after_first_use(self):
        code = pyotp.TOTP(self.secret).now()
        self._post(_fresh_pending(), code)
        assert self.guard.login.call_count == 1
        for variant in (f"{code[:3]} {code[3:]}", f" {code}", f"{code} ", " ".join(code)):
            self._post(_fresh_pending(), variant)
        assert self.guard.login.call_count == 1

    def test_six_failures_across_fresh_sessions_lock_the_user(self):
        wrong = self._wrong()
        for _ in range(tf_mod.MAX_FAILURES):
            session = _fresh_pending()  # a new login each time: the old per-session counter reset here
            self._post(session, wrong)
            assert session.flashed.get("error") == "Invalid code. Please try again."
        # Even the correct code is refused while the user is locked out.
        session = _fresh_pending()
        self._post(session, pyotp.TOTP(self.secret).now())
        self.guard.login.assert_not_called()
        assert "Too many failed attempts" in session.flashed["error"]
        assert "_2fa_pending" not in session.data

    def test_success_resets_the_failure_counter(self):
        wrong = self._wrong()
        for _ in range(tf_mod.MAX_FAILURES - 1):
            self._post(_fresh_pending(), wrong)
        self._post(_fresh_pending(), pyotp.TOTP(self.secret).now())
        assert self.guard.login.call_count == 1
        assert TwoFactor.lockout_remaining(10) == 0
        assert str(10) not in tf_mod._failures

    def test_expired_pending_challenge_is_refused(self):
        stale = _Session({"_2fa_pending": 10, "_2fa_pending_at": int(time.time()) - tf_mod.PENDING_TTL - 5})
        self._post(stale, pyotp.TOTP(self.secret).now())
        self.guard.login.assert_not_called()
        assert "_2fa_pending" not in stale.data

    def test_pending_without_timestamp_is_refused(self):
        legacy = _Session({"_2fa_pending": 10})
        self._post(legacy, pyotp.TOTP(self.secret).now())
        self.guard.login.assert_not_called()

    def test_non_string_code_does_not_crash(self):
        session = _fresh_pending()
        request = _request(session)
        request.input.side_effect = lambda key, default=None: ["123456"]
        with patch.object(ctl, "Auth") as auth, patch.object(ctl.TwoFactor, "decrypt_secret", return_value=self.secret):
            auth._default_guard = self.guard
            ctl.TwoFactorChallengeController().store(request)
        self.guard.login.assert_not_called()


# ---------------------------------------------------------------------------
# Model-backed pieces: recovery codes (AUTH-L3) and the optional step column (AUTH-02)
# ---------------------------------------------------------------------------


class FixTfaUser(Model):
    table = "fix_auth_tfa_users"
    fillable: ClassVar[list[str]] = ["email", "two_factor_recovery_codes", "two_factor_last_used_step"]
    timestamps = False


class FixPlainUser(Model):
    table = "fix_auth_plain_users"
    fillable: ClassVar[list[str]] = ["email"]
    timestamps = False


@pytest.fixture(scope="module", autouse=True)
def _tables():
    Schema.drop_if_exists("fix_auth_tfa_users")
    Schema.drop_if_exists("fix_auth_plain_users")
    Schema.create(
        "fix_auth_tfa_users",
        lambda bp: [
            bp.id(),
            bp.string("email"),
            bp.text("two_factor_recovery_codes").nullable(),
            bp.integer("two_factor_last_used_step").nullable(),
        ],
    )
    Schema.create("fix_auth_plain_users", lambda bp: [bp.id(), bp.string("email")])
    yield
    Schema.drop_if_exists("fix_auth_tfa_users")
    Schema.drop_if_exists("fix_auth_plain_users")


class TestRecoveryCodes:
    def test_recovery_code_works_exactly_once(self):
        codes = TwoFactor.generate_recovery_codes(2)
        hashes = json.dumps([TwoFactor.hash_recovery_code(c) for c in codes])
        user = FixTfaUser.create({"email": "r@example.com", "two_factor_recovery_codes": hashes})
        # Two requests loaded the same row before either one wrote.
        first = FixTfaUser.find(user._attributes["id"])
        second = FixTfaUser.find(user._attributes["id"])
        assert ctl._consume_recovery_code(first, codes[0]) is True
        assert ctl._consume_recovery_code(second, codes[0]) is False
        stored = json.loads(FixTfaUser.find(user._attributes["id"])._attributes["two_factor_recovery_codes"])
        assert len(stored) == 1
        assert ctl._consume_recovery_code(FixTfaUser.find(user._attributes["id"]), codes[1]) is True

    def test_unknown_recovery_code_is_rejected(self):
        hashes = json.dumps([TwoFactor.hash_recovery_code("aaaaaaaaaa-bbbbbbbbbb")])
        user = FixTfaUser.create({"email": "u@example.com", "two_factor_recovery_codes": hashes})
        assert ctl._consume_recovery_code(user, "cccccccccc-dddddddddd") is False

    def test_corrupt_recovery_codes_do_not_crash(self):
        user = FixTfaUser.create({"email": "c@example.com", "two_factor_recovery_codes": "{not json"})
        assert ctl._consume_recovery_code(user, "aaaaaaaaaa-bbbbbbbbbb") is False


class TestStepColumn:
    def test_step_is_persisted_and_blocks_replay_without_shared_store(self):
        secret = TwoFactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        user = FixTfaUser.create({"email": "s@example.com"})
        user = FixTfaUser.find(user._attributes["id"])
        assert ctl._accept_totp(user, code, secret) is True
        stored = FixTfaUser.find(user._attributes["id"])
        assert stored._attributes["two_factor_last_used_step"] == TwoFactor.verify_step(secret, code)
        # Another process: its in-memory replay store has never seen the code.
        with tf_mod._used_codes_lock:
            tf_mod._last_steps.clear()
        assert ctl._accept_totp(stored, f"{code[:3]} {code[3:]}", secret) is False

    def test_apps_without_the_column_keep_working(self):
        secret = TwoFactor.generate_secret()
        code = pyotp.TOTP(secret).now()
        user = FixPlainUser.find(FixPlainUser.create({"email": "p@example.com"})._attributes["id"])
        assert ctl.STEP_COLUMN not in user._attributes
        assert ctl._accept_totp(user, code, secret) is True
        assert ctl._accept_totp(user, code, secret) is False  # still single-use via the store


# ---------------------------------------------------------------------------
# AUTH-L7: confirm page
# ---------------------------------------------------------------------------


class TestSetupConfirm:
    def _controller(self):
        controller = ctl.TwoFactorSetupController()
        controller.view = MagicMock(side_effect=lambda template, data=None: (template, data))
        controller.redirect = MagicMock(side_effect=lambda url, status=302: ("redirect", url))
        return controller

    def test_wrong_code_keeps_the_pending_secret(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-app-key-for-unit-tests-0123456789")
        secret = TwoFactor.generate_secret()
        session = _Session({"_2fa_pending_secret": TwoFactor.encrypt_secret(secret)})
        before = session.data["_2fa_pending_secret"]
        user = MagicMock()
        user._attributes = {"id": 1, "email": "a@b.com"}
        controller = self._controller()
        with patch.object(ctl, "Auth") as auth:
            auth.user.return_value = user
            template, data = controller.confirm(_request(session, "000000", "/two-factor/confirm"))
        assert template == "auth.two_factor.confirm"
        assert data["secret"] == secret
        assert secret in data["qr_url"]
        assert data["error"]
        assert session.data["_2fa_pending_secret"] == before
        user.update.assert_not_called()

    def test_get_confirm_shows_the_same_secret_again(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-app-key-for-unit-tests-0123456789")
        secret = TwoFactor.generate_secret()
        session = _Session({"_2fa_pending_secret": TwoFactor.encrypt_secret(secret)})
        user = MagicMock()
        user._attributes = {"id": 1, "email": "a@b.com"}
        controller = self._controller()
        with patch.object(ctl, "Auth") as auth:
            auth.user.return_value = user
            template, data = controller.show(_request(session, "", "/two-factor/confirm"))
            assert (template, data["secret"]) == ("auth.two_factor.confirm", secret)
            template, _ = controller.show(_request(session, "", "/two-factor/setup"))
            assert template == "auth.two_factor.setup"

    def test_correct_code_enables_two_factor(self, monkeypatch):
        monkeypatch.setenv("APP_KEY", "test-app-key-for-unit-tests-0123456789")
        secret = TwoFactor.generate_secret()
        session = _Session({"_2fa_pending_secret": TwoFactor.encrypt_secret(secret)})
        user = MagicMock()
        user._attributes = {"id": 1, "email": "a@b.com"}
        controller = self._controller()
        with patch.object(ctl, "Auth") as auth:
            auth.user.return_value = user
            template, data = controller.confirm(_request(session, pyotp.TOTP(secret).now(), "/two-factor/confirm"))
        assert template == "auth.two_factor.recovery"
        assert len(data["recovery_codes"]) == 8
        assert user.update.call_args.kwargs["two_factor_enabled"] is True
        assert "_2fa_pending_secret" not in session.data

    def test_template_renders_the_qr_code_without_any_script(self):
        import hunt

        html = (Path(hunt.__file__).parent / "views" / "auth" / "two_factor" / "confirm.html").read_text()
        # The QR code is rendered on the server: the page that shows the TOTP
        # secret loads no script at all, first- or third-party.
        assert "<script" not in html
        assert "cdn." not in html and "jsdelivr" not in html
        assert "{{ qr_svg }}" in html
        # The secret and provisioning URI are always available for manual entry.
        assert "{{ secret }}" in html
        assert "{{ qr_url }}" in html
        assert "{% if error %}" in html
