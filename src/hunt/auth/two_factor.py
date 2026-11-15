from __future__ import annotations

import hmac
import os
import re
import secrets
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# TOTP replay protection store
# ---------------------------------------------------------------------------

# Each verified code is stored for this long (valid_window=1 covers 3x 30s steps).
_TOTP_REPLAY_TTL = 90

# In-memory fallback used when Redis is unavailable (single-process only).
_used_codes: dict[str, float] = {}
_used_codes_lock = threading.Lock()

# Last accepted TOTP time-step per user: {user_id: (step, expires_monotonic)}.
_last_steps: dict[str, tuple[int, float]] = {}

# Per-user challenge failures: {user_id: [failures, record_expires, locked_until]} (wall-clock seconds).
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()

# The first MAX_FAILURES wrong codes are free; every further one must wait out a lockout that
# doubles each time (60s, 120s, ... capped at LOCKOUT_MAX). The record is forgotten after
# FAILURE_TTL seconds without a new failure, or immediately on a successful challenge.
MAX_FAILURES = 5
LOCKOUT_BASE = 60
LOCKOUT_MAX = 3600
FAILURE_TTL = 3600

# How long a password-verified login may wait for its second factor.
PENDING_TTL = 300

_CODE_RE = re.compile(r"[0-9]{6}")

# Atomic "accept only a newer step" for Redis.
_CONSUME_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1]))
if cur and cur >= tonumber(ARGV[1]) then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""


def _redis() -> Any:
    """Return the shared Redis client, or None when Redis is not usable."""
    try:
        from hunt.redis_connection import get_redis

        return get_redis()
    except Exception:
        return None


def _lockout_for(failures: int) -> int:
    """Seconds to lock after the given number of consecutive failures (0 below the limit)."""
    if failures < MAX_FAILURES:
        return 0
    return int(min(LOCKOUT_MAX, LOCKOUT_BASE * (2 ** min(failures - MAX_FAILURES, 16))))


def _mem_mark(key: str) -> None:
    expiry = time.monotonic() + _TOTP_REPLAY_TTL
    with _used_codes_lock:
        now = time.monotonic()
        expired = [k for k, v in _used_codes.items() if v <= now]
        for k in expired:
            del _used_codes[k]
        _used_codes[key] = expiry


def _mem_is_used(key: str) -> bool:
    with _used_codes_lock:
        expiry = _used_codes.get(key)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            del _used_codes[key]
            return False
        return True


def _mem_consume(user_key: str, step: int) -> bool:
    with _used_codes_lock:
        now = time.monotonic()
        for k in [k for k, (_s, exp) in _last_steps.items() if exp <= now]:
            del _last_steps[k]
        current = _last_steps.get(user_key)
        if current is not None and current[0] >= step:
            return False
        _last_steps[user_key] = (step, now + _TOTP_REPLAY_TTL)
        return True


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
    def qr_code_svg(url: str, scale: int = 4, border: int = 2) -> str:
        """Render *url* (a provisioning URI) as an inline ``<svg>`` QR code.

        Rendered on the server so the page that shows a TOTP secret needs no
        JavaScript and no third-party script, and works under a strict
        Content-Security-Policy. The markup contains only path data derived
        from the QR modules, never the URL text itself, so it is safe to embed.
        """
        import io

        import segno

        buffer = io.BytesIO()
        segno.make(url, error="m").save(
            buffer,
            kind="svg",
            scale=scale,
            border=border,
            xmldecl=False,
            svgns=False,
            nl=False,
            dark="#000",
            light="#fff",
        )
        return buffer.getvalue().decode("utf-8")

    @staticmethod
    def normalize_code(code: Any) -> str | None:
        """Canonical six-digit form of a submitted TOTP code, or None if it is not one.

        ASCII spaces are removed (authenticator apps display ``123 456``). Everything that
        handles a code (verification, replay protection) must use this one canonical form.
        """
        if not isinstance(code, str):
            return None
        candidate = code.replace(" ", "")
        return candidate if _CODE_RE.fullmatch(candidate) else None

    @staticmethod
    def verify_step(secret: str, code: Any, valid_window: int = 1) -> int | None:
        """Return the time-step counter the code is valid for, or None when it is invalid.

        The counter identifies the 30-second step that produced the code; pass it to
        :meth:`consume` so that a code (in any spelling) is accepted at most once.
        """
        import pyotp

        canonical = TwoFactor.normalize_code(code)
        if canonical is None or not secret:
            return None
        try:
            totp = pyotp.TOTP(secret)
            current = int(time.time()) // totp.interval
            matched: int | None = None
            for offset in range(-valid_window, valid_window + 1):
                expected = totp.generate_otp(current + offset)
                if hmac.compare_digest(expected, canonical) and matched is None:
                    matched = current + offset
            return matched
        except Exception:
            return None

    @staticmethod
    def verify(secret: str, code: str) -> bool:
        return TwoFactor.verify_step(secret, code) is not None

    @staticmethod
    def generate_recovery_codes(n: int = 8) -> list[str]:
        return [secrets.token_hex(5) + "-" + secrets.token_hex(5) for _ in range(n)]

    # ------------------------------------------------------------------
    # Replay protection
    # ------------------------------------------------------------------

    @staticmethod
    def consume(user_id: Any, step: int) -> bool:
        """Atomically accept a TOTP time-step for a user. True exactly once per step.

        The last accepted step is remembered per user and anything not strictly newer is
        refused, so a code cannot be replayed (whatever its spelling) and an older code
        from the validity window cannot be used after a newer one. Redis is used when
        available (one atomic script); otherwise a locked in-process store.
        """
        step = int(step)
        key = f"hunt:2fa:step:{user_id}"
        client = _redis()
        if client is not None:
            try:
                return bool(int(client.eval(_CONSUME_LUA, 1, key, step, _TOTP_REPLAY_TTL)))
            except Exception:
                pass
        return _mem_consume(key, step)

    # ------------------------------------------------------------------
    # Challenge brute-force protection (per user, independent of the session)
    # ------------------------------------------------------------------

    @staticmethod
    def register_attempt(user_id: Any) -> bool:
        """Count a challenge attempt *before* verifying it. False means the user is locked out.

        The counter is keyed on the user, so starting a fresh session (logging in again
        with the password) does not grant more guesses. Call :meth:`clear_failures` after
        a successful challenge.
        """
        fail_key = f"hunt:2fa:fail:{user_id}"
        lock_key = f"hunt:2fa:lock:{user_id}"
        client = _redis()
        if client is not None:
            try:
                if client.exists(lock_key):
                    return False
                failures = int(client.incr(fail_key))
                client.expire(fail_key, FAILURE_TTL)
                lockout = _lockout_for(failures)
                if failures <= MAX_FAILURES:
                    if lockout:
                        client.set(lock_key, "1", ex=lockout)
                    return True
                # Past the limit: exactly one caller per lockout period may try.
                return bool(client.set(lock_key, "1", nx=True, ex=lockout))
            except Exception:
                pass
        now = time.time()
        key = str(user_id)
        with _failures_lock:
            for k in [k for k, rec in _failures.items() if rec[1] <= now and rec[2] <= now]:
                del _failures[k]
            record = _failures.get(key)
            if record is None:
                record = [0, 0.0, 0.0]
                _failures[key] = record
            if record[2] > now:
                return False
            record[0] += 1
            record[1] = now + FAILURE_TTL
            lockout = _lockout_for(int(record[0]))
            if lockout:
                record[2] = now + lockout
            return True

    @staticmethod
    def clear_failures(user_id: Any) -> None:
        """Forget the failure counter after a successful challenge."""
        client = _redis()
        if client is not None:
            try:
                client.delete(f"hunt:2fa:fail:{user_id}", f"hunt:2fa:lock:{user_id}")
            except Exception:
                pass
        with _failures_lock:
            _failures.pop(str(user_id), None)

    @staticmethod
    def lockout_remaining(user_id: Any) -> int:
        """Seconds until the user may attempt a challenge again (0 when not locked)."""
        client = _redis()
        if client is not None:
            try:
                ttl = int(client.ttl(f"hunt:2fa:lock:{user_id}"))
                if ttl > 0:
                    return ttl
            except Exception:
                pass
        with _failures_lock:
            record = _failures.get(str(user_id))
            if record is None:
                return 0
            return max(0, int(record[2] - time.time() + 0.999))

    @staticmethod
    def mark_used(user_id: Any, code: str) -> None:
        """Record that this TOTP code has been used for the given user.

        Deprecated: check-then-mark is not atomic. Use :meth:`verify_step` and
        :meth:`consume` instead. The code is canonicalised so that ``"123 456"`` and
        ``"123456"`` share one entry.
        """
        code = TwoFactor.normalize_code(code) or str(code)
        key = f"hunt:2fa:used:{user_id}:{code}"
        try:
            from hunt.redis_connection import get_redis

            get_redis().set(key, "1", ex=_TOTP_REPLAY_TTL)
            return
        except Exception:
            pass
        _mem_mark(key)

    @staticmethod
    def is_used(user_id: Any, code: str) -> bool:
        """Return True if this code was already accepted for the given user.

        Deprecated: see :meth:`mark_used`.
        """
        code = TwoFactor.normalize_code(code) or str(code)
        key = f"hunt:2fa:used:{user_id}:{code}"
        try:
            from hunt.redis_connection import get_redis

            return bool(get_redis().get(key))
        except Exception:
            pass
        return _mem_is_used(key)

    # ------------------------------------------------------------------
    # TOTP secret encryption (delegated to hunt.support.crypt)
    # ------------------------------------------------------------------

    @staticmethod
    def encrypt_secret(secret: str) -> str:
        from hunt.support.crypt import encrypt

        return encrypt(secret)

    @staticmethod
    def decrypt_secret(token: str) -> str:
        from hunt.support.crypt import decrypt

        return decrypt(token)

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
