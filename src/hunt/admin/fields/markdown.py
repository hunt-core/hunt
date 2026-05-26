from __future__ import annotations

import re
from typing import Any

from hunt.admin.field import Field

_TAG_RE = re.compile(r"<[^>]+>")

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
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_ALLOWED_ATTRS = {
    "a": {"href", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align"},
    "td": {"align"},
}


def _render_markdown(raw: str) -> str:
    try:
        import markdown as md_lib

        html = md_lib.markdown(raw, extensions=["tables", "fenced_code", "nl2br"])
    except ImportError:
        escaped = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = escaped.replace("\n", "<br>")
    try:
        import nh3

        return nh3.clean(
            html,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRS,
            link_rel="noopener noreferrer nofollow",
        )
    except ImportError:
        return _TAG_RE.sub("", html)


class Markdown(Field):
    field_type: str = "markdown"

    def __init__(self, name: str, attribute: str | None = None) -> None:
        super().__init__(name, attribute)
        self.hide_from_index()

    def render_html(self, instance: Any) -> str:
        raw = instance._attributes.get(self.attribute) if hasattr(instance, "_attributes") else None
        if not raw:
            return ""
        return _render_markdown(str(raw))

    def display_value(self, instance: Any) -> str:
        raw = instance._attributes.get(self.attribute) if hasattr(instance, "_attributes") else None
        if not raw:
            return ""
        plain = _TAG_RE.sub("", str(raw))
        return plain[:200] + "…" if len(plain) > 200 else plain
