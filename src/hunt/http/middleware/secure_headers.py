from __future__ import annotations

import os

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response


class SecureHeaders(Middleware):
    """Add defensive security headers to every response.

    All values can be overridden by subclassing or setting env vars:
      SECURE_HSTS_SECONDS      — max-age for Strict-Transport-Security (default 31536000)
      SECURE_HSTS_SUBDOMAINS   — include subdomains in HSTS (default false)
      SECURE_FRAME_OPTIONS     — X-Frame-Options value (default SAMEORIGIN)
      SECURE_CONTENT_TYPE_NOSNIFF — set X-Content-Type-Options: nosniff (default true)
      SECURE_REFERRER_POLICY   — Referrer-Policy value (default strict-origin-when-cross-origin)
      SECURE_CONTENT_SECURITY_POLICY — raw CSP header value (default "default-src 'self'")
    """

    async def handle(self, request: Request, next: Next) -> Response:
        response = await next(request)
        self._apply(response)
        return response

    def _apply(self, response: Response) -> None:
        # X-Frame-Options — prevent clickjacking
        frame_opt = os.environ.get("SECURE_FRAME_OPTIONS", "SAMEORIGIN")
        if frame_opt:
            response.header("X-Frame-Options", frame_opt)

        # X-Content-Type-Options — stop MIME-type sniffing
        nosniff = os.environ.get("SECURE_CONTENT_TYPE_NOSNIFF", "true").lower() != "false"
        if nosniff:
            response.header("X-Content-Type-Options", "nosniff")

        # Referrer-Policy
        referrer = os.environ.get("SECURE_REFERRER_POLICY", "strict-origin-when-cross-origin")
        if referrer:
            response.header("Referrer-Policy", referrer)

        # Strict-Transport-Security — 1 year by default; set to 0 to disable (local dev)
        hsts_seconds = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
        if hsts_seconds > 0:
            hsts = f"max-age={hsts_seconds}"
            if os.environ.get("SECURE_HSTS_SUBDOMAINS", "false").lower() == "true":
                hsts += "; includeSubDomains"
            response.header("Strict-Transport-Security", hsts)

        # Content-Security-Policy — restricts resource origins; override via env var.
        # Skip if the response already carries a CSP (e.g. set by a route-specific handler).
        if "Content-Security-Policy" not in response._headers:
            csp = os.environ.get("SECURE_CONTENT_SECURITY_POLICY", "default-src 'self'")
            if csp:
                response.header("Content-Security-Policy", csp)
