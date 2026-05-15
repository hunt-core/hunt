from __future__ import annotations

import re
from typing import Any

from hunt.admin.field import Field

_TAG_RE = re.compile(r"<[^>]+>")

# Tags and attributes produced by Trix that are safe to render.
_ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "ul",
}
_ALLOWED_ATTRS = {"a": {"href", "target", "rel"}}


def _sanitize_html(raw: str) -> str:
    try:
        import nh3

        return nh3.clean(
            raw,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRS,
            link_rel="noopener noreferrer nofollow",
        )
    except ImportError:
        # Fallback: strip all tags. Install nh3 for rich-text rendering.
        return _TAG_RE.sub("", raw)


class RichText(Field):
    field_type: str = "richtext"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self.hide_from_index()

    def value_for(self, instance: Any) -> str:
        raw = instance._attributes.get(self.attribute) if hasattr(instance, "_attributes") else None
        if not raw:
            return ""
        return _sanitize_html(str(raw))

    def display_value(self, instance: Any) -> str:
        val = self.value_for(instance)
        if not val:
            return ""
        plain = _TAG_RE.sub("", val)
        return plain[:200] + "…" if len(plain) > 200 else plain
