from __future__ import annotations

import gzip
import os
from typing import ClassVar

from hunt.http.middleware import Middleware, Next
from hunt.http.request import Request
from hunt.http.response import Response

# Content types worth compressing — text and text-like binary formats. Already
# compressed formats (images, video, fonts, zips) are skipped: re-compressing
# them wastes CPU for no gain.
_DEFAULT_COMPRESSIBLE: tuple[str, ...] = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "application/manifest+json",
    "image/svg+xml",
)


class CompressResponse(Middleware):
    """gzip-compress text responses when the client advertises support.

    Add it near the top of the global middleware stack so it sees the final
    response body. Skips responses that are already encoded, too small, or of a
    non-text content type.

    Tunable via env:
      GZIP_ENABLED      — set to "false" to disable (default enabled when in stack)
      GZIP_MIN_LENGTH   — minimum body size in bytes to bother compressing (default 1024)
      GZIP_LEVEL        — zlib compression level 1-9 (default 6)
    """

    reads_body = False  # never touches the request body

    compressible_types: ClassVar[tuple[str, ...]] = _DEFAULT_COMPRESSIBLE

    async def handle(self, request: Request, next: Next) -> Response:
        response = await next(request)

        if os.environ.get("GZIP_ENABLED", "true").lower() == "false":
            return response
        if not self._client_accepts_gzip(request):
            return response
        if not self._should_compress(response):
            return response

        level = self._int_env("GZIP_LEVEL", 6, lo=1, hi=9)
        response._body = gzip.compress(response._body, compresslevel=level)
        response.header("Content-Encoding", "gzip")
        self._add_vary(response, "Accept-Encoding")
        return response

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _client_accepts_gzip(request: Request) -> bool:
        """True when Accept-Encoding allows gzip (``gzip;q=0`` is a refusal)."""
        accept = request.header("accept-encoding", "") or ""
        gzip_q: float | None = None
        star_q: float | None = None
        for item in accept.lower().split(","):
            coding, _, params = item.strip().partition(";")
            coding = coding.strip()
            if coding not in ("gzip", "x-gzip", "*"):
                continue
            q = 1.0
            for param in params.split(";"):
                name, _, value = param.strip().partition("=")
                if name.strip() == "q":
                    try:
                        q = float(value.strip())
                    except ValueError:
                        q = 0.0
            if coding == "*":
                star_q = q
            else:
                gzip_q = q if gzip_q is None else max(gzip_q, q)
        if gzip_q is not None:
            return gzip_q > 0
        return star_q is not None and star_q > 0

    def _should_compress(self, response: Response) -> bool:
        # Don't double-encode or touch bodiless/cache-revalidation responses.
        if any(k.lower() == "content-encoding" for k in response._headers):
            return False
        if response.status < 200 or response.status in (204, 304):
            return False
        min_length = self._int_env("GZIP_MIN_LENGTH", 1024, lo=0, hi=None)
        if len(response._body) < min_length:
            return False
        ctype = (response.content_type or "").split(";")[0].strip().lower()
        return ctype.startswith(self.compressible_types) or ctype in self.compressible_types

    @staticmethod
    def _add_vary(response: Response, value: str) -> None:
        response.vary(value)

    @staticmethod
    def _int_env(name: str, default: int, *, lo: int | None, hi: int | None) -> int:
        try:
            val = int(os.environ.get(name, str(default)))
        except ValueError:
            return default
        if lo is not None:
            val = max(lo, val)
        if hi is not None:
            val = min(hi, val)
        return val
