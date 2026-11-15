from __future__ import annotations

import json
import logging
import time
from typing import Any

from hunt.auth.manager import Auth, verify_password
from hunt.auth.two_factor import TwoFactor
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response

_log = logging.getLogger("hunt.auth")

# Optional column on the users table. When an application has it, the last accepted TOTP
# time-step is also persisted there, which keeps replay protection working across
# processes without Redis. Applications without the column are unaffected.
STEP_COLUMN = "two_factor_last_used_step"


def _accept_totp(user: Any, code: Any, secret: str) -> bool:
    """Verify a TOTP code and consume its time-step exactly once for this user."""
    step = TwoFactor.verify_step(secret, code)
    if step is None:
        return False
    attrs = user._attributes
    user_id = attrs.get("id")
    has_column = isinstance(attrs, dict) and STEP_COLUMN in attrs
    last = attrs.get(STEP_COLUMN) if has_column else None
    if last is not None:
        try:
            if int(last) >= step:
                return False
        except (TypeError, ValueError):
            last = None
    if not TwoFactor.consume(user_id, step):
        return False
    if has_column:
        try:
            query = type(user).where("id", user_id)
            if last is not None:
                query = query.where(STEP_COLUMN, last)
            affected = query.update({STEP_COLUMN: step})
            if last is not None and not affected:
                return False  # another request advanced the step first
            attrs[STEP_COLUMN] = step
        except Exception:
            _log.exception("Could not persist the two-factor time-step for user %s", user_id)
            return False
    return True


def _consume_recovery_code(user: Any, code: str) -> bool:
    """Match a recovery code and remove it with a compare-and-set so it works only once."""
    raw = user._attributes.get("two_factor_recovery_codes") or "[]"
    try:
        hashes = json.loads(raw)
    except Exception:
        return False
    if not isinstance(hashes, list):
        return False
    matched = next((h for h in hashes if isinstance(h, str) and TwoFactor.verify_recovery_code(code, h)), None)
    if matched is None:
        return False
    hashes.remove(matched)
    remaining = json.dumps(hashes)
    # Conditional update: only the request that still sees the old list wins.
    affected = (
        type(user)
        .where("id", user._attributes.get("id"))
        .where("two_factor_recovery_codes", raw)
        .update({"two_factor_recovery_codes": remaining})
    )
    if not affected:
        return False
    user._attributes["two_factor_recovery_codes"] = remaining
    return True


def _clear_pending(session: Any) -> None:
    for key in ("_2fa_pending", "_2fa_pending_at", "_2fa_attempts"):
        session.forget(key)


def _pending_user_id(session: Any) -> Any:
    """Return the user id awaiting a second factor, or None when absent or expired."""
    from hunt.auth.two_factor import PENDING_TTL

    pending_id = session.get("_2fa_pending")
    if not pending_id:
        return None
    started = session.get("_2fa_pending_at")
    if isinstance(started, bool) or not isinstance(started, (int, float)) or time.time() - started > PENDING_TTL:
        _clear_pending(session)
        return None
    return pending_id


class TwoFactorSetupController(Controller):
    """Enable two-factor authentication for the authenticated user."""

    def show(self, request: Request) -> Response:
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        if user._attributes.get("two_factor_enabled"):
            request.session().flash("info", "Two-factor authentication is already enabled.")
            return self.redirect("/two-factor/manage")
        # GET /two-factor/confirm with a secret already pending: show the same QR code
        # again instead of asking for the password and generating a new secret.
        if str(getattr(request, "path", "")).rstrip("/").endswith("/confirm"):
            pending = self._pending_secret(request)
            if pending is not None:
                return self._confirm_view(user, pending)
        return self.view("auth.two_factor.setup", {"user": user})

    def store(self, request: Request) -> Response:
        """Confirm password, generate secret, store in session pending confirmation."""
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        password = request.input("password", "")
        if not verify_password(password, user._attributes.get("password", "")):
            request.session().flash("error", "Incorrect password.")
            return self.redirect("/two-factor/setup")
        secret = TwoFactor.generate_secret()
        request.session().put("_2fa_pending_secret", TwoFactor.encrypt_secret(secret))
        return self._confirm_view(user, secret)

    def _pending_secret(self, request: Request) -> str | None:
        encrypted = request.session().get("_2fa_pending_secret")
        if not encrypted:
            return None
        try:
            return TwoFactor.decrypt_secret(encrypted)
        except Exception:
            request.session().forget("_2fa_pending_secret")
            return None

    def _confirm_view(self, user: Any, secret: str, error: str | None = None) -> Response:
        from markupsafe import Markup

        qr_url = TwoFactor.qr_code_url(secret, user._attributes.get("email", ""))
        # Generated markup (path data only), so it is safe to emit unescaped.
        qr_svg = Markup(TwoFactor.qr_code_svg(qr_url))
        return self.view(
            "auth.two_factor.confirm",
            {"qr_url": qr_url, "qr_svg": qr_svg, "secret": secret, "error": error},
        )

    def confirm(self, request: Request) -> Response:
        """Verify the user-entered TOTP code and activate 2FA."""
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        secret = self._pending_secret(request)
        if secret is None:
            return self.redirect("/two-factor/setup")
        code = request.input("code", "")
        if not _accept_totp(user, code, secret):
            # Keep the pending secret: the authenticator app already holds it, so a typo
            # must not force the user to scan a different QR code.
            return self._confirm_view(user, secret, "Invalid verification code. Please try again.")
        recovery_codes = TwoFactor.generate_recovery_codes()
        hashed_codes = [TwoFactor.hash_recovery_code(c) for c in recovery_codes]
        user.update(
            two_factor_secret=TwoFactor.encrypt_secret(secret),
            two_factor_enabled=True,
            two_factor_recovery_codes=json.dumps(hashed_codes),
        )
        request.session().forget("_2fa_pending_secret")
        request.session().flash("success", "Two-factor authentication has been enabled.")
        return self.view("auth.two_factor.recovery", {"recovery_codes": recovery_codes})

    def destroy(self, request: Request) -> Response:
        """Disable 2FA after confirming the password."""
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        password = request.input("password", "")
        if not verify_password(password, user._attributes.get("password", "")):
            request.session().flash("error", "Incorrect password.")
            return self.redirect("/two-factor/manage")
        user.update(
            two_factor_secret=None,
            two_factor_enabled=False,
            two_factor_recovery_codes=None,
        )
        request.session().flash("success", "Two-factor authentication has been disabled.")
        return self.redirect("/two-factor/manage")


class TwoFactorChallengeController(Controller):
    """Handle the 2FA challenge during login."""

    def show(self, request: Request) -> Response:
        if not _pending_user_id(request.session()):
            return self.redirect("/login")
        return self.view("auth.two_factor.challenge")

    def store(self, request: Request) -> Response:
        """Validate the TOTP code (or recovery code) and complete login."""
        session = request.session()
        pending_id = _pending_user_id(session)
        if not pending_id:
            return self.redirect("/login")

        guard = Auth._default_guard
        if guard._model is None:
            return self.redirect("/login")
        user = guard._model.find(pending_id)
        if user is None:
            _clear_pending(session)
            return self.redirect("/login")

        # Count the attempt against the *user* before looking at the code. The counter
        # lives outside the session, so logging in again does not buy more guesses.
        if not TwoFactor.register_attempt(pending_id):
            _clear_pending(session)
            session.flash("error", "Too many failed attempts. Please try again later.")
            return self.redirect("/login")

        raw_code = request.input("code", "")
        code = raw_code.strip() if isinstance(raw_code, str) else ""
        encrypted_secret = user._attributes.get("two_factor_secret", "")
        try:
            secret = TwoFactor.decrypt_secret(encrypted_secret)
        except Exception:
            # Decryption failure — either APP_KEY was rotated after enrollment or the
            # stored value is corrupt. Clear the pending session so the user is not
            # trapped in the challenge loop and can attempt to log in again.
            _clear_pending(session)
            session.flash(
                "error",
                "Two-factor authentication is temporarily unavailable. Please contact support.",
            )
            return self.redirect("/login")

        if _accept_totp(user, code, secret):
            TwoFactor.clear_failures(pending_id)
            _clear_pending(session)
            guard.login(user)
            return self.redirect("/dashboard")

        # Try recovery codes. Recovery codes contain a hyphen ("aaaaa-bbbbb");
        # TOTP codes are all digits — skip the expensive bcrypt scan for TOTP-shaped input.
        if "-" in code and _consume_recovery_code(user, code):
            TwoFactor.clear_failures(pending_id)
            _clear_pending(session)
            guard.login(user)
            session.flash("warning", "Recovery code used. Please regenerate your recovery codes.")
            return self.redirect("/dashboard")

        session.flash("error", "Invalid code. Please try again.")
        return self.redirect("/two-factor/challenge")


class TwoFactorManageController(Controller):
    """Show the 2FA management page (regenerate recovery codes, disable)."""

    def show(self, request: Request) -> Response:
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        raw = user._attributes.get("two_factor_recovery_codes") or "[]"
        try:
            hashes: list[str] = json.loads(raw)
        except Exception:
            hashes = []
        return self.view(
            "auth.two_factor.manage",
            {
                "user": user,
                "enabled": bool(user._attributes.get("two_factor_enabled")),
                "recovery_code_count": len(hashes),
            },
        )

    def regenerate(self, request: Request) -> Response:
        """Regenerate recovery codes."""
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        password = request.input("password", "")
        if not verify_password(password, user._attributes.get("password", "")):
            request.session().flash("error", "Incorrect password.")
            return self.redirect("/two-factor/manage")
        recovery_codes = TwoFactor.generate_recovery_codes()
        hashed_codes = [TwoFactor.hash_recovery_code(c) for c in recovery_codes]
        user.update(two_factor_recovery_codes=json.dumps(hashed_codes))
        request.session().flash("success", "Recovery codes have been regenerated.")
        return self.view("auth.two_factor.recovery", {"recovery_codes": recovery_codes})
