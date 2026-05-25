from __future__ import annotations

import json

from hunt.auth.manager import Auth, verify_password
from hunt.auth.two_factor import TwoFactor
from hunt.http.controller import Controller
from hunt.http.request import Request
from hunt.http.response import Response


class TwoFactorSetupController(Controller):
    """Enable two-factor authentication for the authenticated user."""

    def show(self, request: Request) -> Response:
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        if user._attributes.get("two_factor_enabled"):
            request.session().flash("info", "Two-factor authentication is already enabled.")
            return self.redirect("/two-factor/manage")
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
        qr_url = TwoFactor.qr_code_url(secret, user._attributes.get("email", ""))
        request.session().put("_2fa_pending_secret", secret)
        return self.view("auth.two_factor.confirm", {"qr_url": qr_url, "secret": secret})

    def confirm(self, request: Request) -> Response:
        """Verify the user-entered TOTP code and activate 2FA."""
        user = Auth.user()
        if user is None:
            return self.redirect("/login")
        secret = request.session().get("_2fa_pending_secret")
        if not secret:
            return self.redirect("/two-factor/setup")
        code = request.input("code", "")
        if not TwoFactor.verify(secret, code):
            request.session().flash("error", "Invalid verification code. Please try again.")
            return self.redirect("/two-factor/confirm")
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
        if not request.session().get("_2fa_pending"):
            return self.redirect("/login")
        return self.view("auth.two_factor.challenge")

    def store(self, request: Request) -> Response:
        """Validate the TOTP code (or recovery code) and complete login."""
        pending_id = request.session().get("_2fa_pending")
        if not pending_id:
            return self.redirect("/login")

        guard = Auth._default_guard
        if guard._model is None:
            return self.redirect("/login")
        user = guard._model.find(pending_id)
        if user is None:
            request.session().forget("_2fa_pending")
            return self.redirect("/login")

        code = request.input("code", "").strip()
        encrypted_secret = user._attributes.get("two_factor_secret", "")
        try:
            secret = TwoFactor.decrypt_secret(encrypted_secret)
        except Exception:
            # Decryption failure — either APP_KEY was rotated after enrollment or the
            # stored value is corrupt. Clear the pending session so the user is not
            # trapped in the challenge loop and can attempt to log in again.
            request.session().forget("_2fa_pending")
            request.session().flash(
                "error",
                "Two-factor authentication is temporarily unavailable. Please contact support.",
            )
            return self.redirect("/login")

        if TwoFactor.verify(secret, code):
            request.session().forget("_2fa_pending")
            guard.login(user)
            return self.redirect("/dashboard")

        # Try recovery codes. Recovery codes contain a hyphen ("aaaaa-bbbbb");
        # TOTP codes are all digits — skip the expensive bcrypt scan for TOTP-shaped input.
        raw = user._attributes.get("two_factor_recovery_codes") or "[]"
        try:
            hashes: list[str] = json.loads(raw)
        except Exception:
            hashes = []

        matched = None
        if "-" in code:
            matched = next((h for h in hashes if TwoFactor.verify_recovery_code(code, h)), None)
        if matched is not None:
            hashes.remove(matched)
            user.update(two_factor_recovery_codes=json.dumps(hashes))
            request.session().forget("_2fa_pending")
            guard.login(user)
            request.session().flash("warning", "Recovery code used. Please regenerate your recovery codes.")
            return self.redirect("/dashboard")

        request.session().flash("error", "Invalid code. Please try again.")
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
