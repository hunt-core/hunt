from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class PaginationResult:
    """Wraps paginated query results with metadata and link helpers.

    Backwards-compatible with the plain dict that ``paginate()`` previously
    returned: attribute access (``result.total``), item access
    (``result["data"]``), and iteration (``for item in result``) all work.
    """

    def __init__(
        self,
        *,
        data: list,
        total: int | None,
        per_page: int,
        current_page: int,
        last_page: int | None,
        has_more_pages: bool | None = None,
    ) -> None:
        self.data = data
        self.total = total
        self.per_page = per_page
        self.current_page = current_page
        self.last_page = last_page
        if has_more_pages is not None:
            self.has_more_pages = has_more_pages
        elif last_page is not None:
            self.has_more_pages = current_page < last_page
        else:
            self.has_more_pages = False

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _page_url(self, base_url: str, page: int) -> str:
        parsed = urlparse(base_url)
        params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        params["page"] = str(page)
        return urlunparse(parsed._replace(query=urlencode(params)))

    def prev_page_url(self, base_url: str) -> str | None:
        if self.current_page <= 1:
            return None
        return self._page_url(base_url, self.current_page - 1)

    def next_page_url(self, base_url: str) -> str | None:
        if not self.has_more_pages:
            return None
        return self._page_url(base_url, self.current_page + 1)

    def links(self, base_url: str = "") -> list[dict[str, Any]]:
        """Return a list of link descriptors suitable for rendering a pagination bar.

        Each item has a ``type`` key: ``"prev"``, ``"next"``, ``"page"``, or
        ``"ellipsis"``.  Page items also carry ``label`` (str) and ``active``
        (bool).  Prev/next items carry ``url`` (str or None) and
        ``disabled`` (bool).
        """
        result: list[dict[str, Any]] = []

        prev_url = self.prev_page_url(base_url)
        result.append({"type": "prev", "url": prev_url, "disabled": prev_url is None})

        if self.last_page is None:
            # simple_paginate — no page numbers, only prev/next
            next_url = self.next_page_url(base_url)
            result.append({"type": "next", "url": next_url, "disabled": next_url is None})
            return result

        # Full pagination — build windowed page list
        last = self.last_page
        cur = self.current_page
        window = 2  # pages on each side of current

        visible: set[int] = {1, last}
        for p in range(max(1, cur - window), min(last, cur + window) + 1):
            visible.add(p)

        prev_p: int | None = None
        for p in sorted(visible):
            if prev_p is not None and p - prev_p > 1:
                result.append({"type": "ellipsis"})
            result.append(
                {
                    "type": "page",
                    "url": self._page_url(base_url, p),
                    "label": str(p),
                    "active": p == cur,
                }
            )
            prev_p = p

        next_url = self.next_page_url(base_url)
        result.append({"type": "next", "url": next_url, "disabled": next_url is None})
        return result

    # ------------------------------------------------------------------
    # Backwards-compatibility shims
    # ------------------------------------------------------------------

    _KEYS = ("data", "total", "per_page", "current_page", "last_page")

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __contains__(self, key: object) -> bool:
        return key in self._KEYS

    def keys(self):
        return self._KEYS

    def items(self):
        return ((k, getattr(self, k)) for k in self._KEYS)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self._KEYS}

    def __repr__(self) -> str:
        return (
            f"PaginationResult(page={self.current_page}/{self.last_page}, total={self.total}, per_page={self.per_page})"
        )
