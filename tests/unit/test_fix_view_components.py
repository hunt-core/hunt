"""VIEW-01: bundled components escape their props; only slot content is emitted as markup."""

from __future__ import annotations

from pathlib import Path

import pytest
from markupsafe import Markup

from hunt.view.factory import ViewFactory, safe_url

XSS = "<script>alert(1)</script>"
QUOTES = "\"'><img src=x onerror=alert(1)>"


def render(tmp_path: Path, source: str, data: dict | None = None) -> str:
    (tmp_path / "page.html").write_text(source, encoding="utf-8")
    return ViewFactory(tmp_path).make("page", data or {}).render()


def assert_clean(html: str) -> None:
    assert "<script" not in html
    assert "<img" not in html
    assert "onerror=alert(1)>" not in html.replace("onerror=alert(1)&gt;", "")


@pytest.mark.parametrize(
    "source",
    [
        "@component('alert', {'message': v, 'title': v, 'type': v})",
        "@component('badge', {'text': v, 'color': v})",
        "@component('button', {'label': v, 'type': v, 'class': v, 'variant': v})",
        "@component('card', {'body': v, 'title': v, 'subtitle': v, 'class': v})",
        "@component('empty-state', {'title': v, 'description': v, 'icon': v, 'action_url': v, 'action_label': v})",
        "@component('form-group', {'name': v, 'label': v, 'value': v, 'placeholder': v, 'type': v})",
        "@component('form-group', {'name': v, 'type': 'textarea', 'value': v, 'rows': v})",
        "@component('form-group', {'name': 'n', 'type': 'select', 'value': v, 'options': [v, {'value': v, 'label': v}]})",
        "@component('modal', {'id': v, 'title': v})",
        "@component('navbar', {'brand': v, 'brand_url': v, 'links': [{'url': v, 'label': v}]})",
        "@component('sidebar', {'brand': v, 'links': [{'url': v, 'label': v, 'icon': v}]})",
        "@component('table', {'headers': [v], 'rows': [[v], {v: v}]})",
    ],
)
@pytest.mark.parametrize("payload", [XSS, QUOTES])
def test_props_are_escaped(tmp_path, source, payload):
    html = render(tmp_path, source, {"v": payload})
    assert_clean(html)
    # the payload can never close an attribute: every double quote in it is escaped
    assert payload not in html


def test_alert_message_prop_is_escaped_and_slot_is_markup(tmp_path):
    html = render(tmp_path, "@component('alert', {'message': flash})", {"flash": XSS})
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    html = render(tmp_path, "@component('alert')<strong>{{ $v }}</strong>@endcomponent", {"v": XSS})
    assert "<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong>" in html


def test_button_and_card_render_slot_markup(tmp_path):
    html = render(tmp_path, "@component('button')<em>Go</em>@endcomponent")
    assert "<em>Go</em></button>" in html
    html = render(
        tmp_path,
        "@component('card', {'title': 'T'})<p>Body</p>@slot('footer')<a href=\"/x\">more</a>@endslot@endcomponent",
    )
    assert "<p>Body</p>" in html
    assert '<a href="/x">more</a>' in html


def test_slot_variable_passed_as_plain_string_is_escaped(tmp_path):
    """A caller that injects _slot_default as data does not get a raw-HTML sink."""
    for name in ("alert", "button", "card", "modal", "navbar", "empty-state"):
        html = render(tmp_path, "{% include 'components/" + name + ".html' %}", {"_slot_default": XSS})
        assert_clean(html)
    for name in ("card", "modal", "sidebar"):
        html = render(tmp_path, "{% include 'components/" + name + ".html' %}", {"_slot_footer": XSS})
        assert_clean(html)


def test_modal_id_is_not_placed_in_a_script_context(tmp_path):
    payload = "');alert(1);//"
    html = render(tmp_path, "@component('modal', {'id': v, 'title': 'T'})Body@endcomponent", {"v": payload})
    assert "onclick=\"this.closest('dialog').close()\"" in html
    assert html.count("alert(1)") == 1  # only inside the (escaped) id attribute
    assert 'id="&#39;);alert(1);//"' in html
    assert "getElementById" not in html


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "\x01javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
    ],
)
def test_href_props_reject_script_urls(tmp_path, url):
    sources = [
        "@component('navbar', {'brand': 'B', 'brand_url': u, 'links': [{'url': u, 'label': 'L'}]})",
        "@component('sidebar', {'links': [{'url': u, 'label': 'L'}]})",
        "@component('empty-state', {'action_url': u})",
    ]
    for source in sources:
        html = render(tmp_path, source, {"u": url})
        assert "script:" not in html.lower()
        assert "data:" not in html
        assert 'href="#"' in html


def test_href_props_keep_ordinary_urls(tmp_path):
    html = render(
        tmp_path,
        "@component('navbar', {'brand_url': '/home', 'links': links})",
        {
            "links": [
                {"url": "/posts?a=1&b=2", "label": "P"},
                {"url": "https://example.com/x", "label": "E"},
                {"url": "mailto:a@b.co", "label": "M"},
                {"url": "#top", "label": "T"},
            ]
        },
    )
    for expected in (
        'href="/home"',
        'href="/posts?a=1&amp;b=2"',
        'href="https://example.com/x"',
        'href="mailto:a@b.co"',
    ):
        assert expected in html
    assert 'href="#top"' in html


def test_safe_url_filter():
    assert safe_url("/a/b") == "/a/b"
    assert safe_url("posts/1") == "posts/1"
    assert safe_url("?page=2") == "?page=2"
    assert safe_url("//cdn.example.com/x") == "//cdn.example.com/x"
    assert safe_url("HTTPS://example.com") == "HTTPS://example.com"
    assert safe_url("tel:+123") == "tel:+123"
    assert safe_url("/redirect?to=javascript:x") == "/redirect?to=javascript:x"
    assert safe_url(None) == ""
    assert safe_url("javascript:alert(1)") == "#"
    assert safe_url("javascript:alert(1)", "/") == "/"
    assert safe_url("file:///etc/passwd") == "#"
    # Markup is not escaped again by Jinja, so entity-encoded schemes are judged decoded
    assert safe_url(Markup("&#106;avascript:alert(1)")) == "#"
    assert safe_url(Markup("javascript&colon;alert(1)")) == "#"
    result = safe_url(Markup("/a?x=1&amp;y=2"))
    assert result == "/a?x=1&y=2"
    assert not isinstance(result, Markup)


def test_form_group_shows_the_first_error_message(tmp_path):
    source = "@component('form-group', {'name': 'email', 'value': 'x'})"
    html = render(tmp_path, source, {"errors": {"email": ["Invalid <email>.", "Second."]}})
    assert '<p class="text-red-600 text-xs mt-1">Invalid &lt;email&gt;.</p>' in html
    assert "[" not in html.split("text-red-600")[1]
    assert "Second." not in html
    assert "border-red-400" in html

    html = render(tmp_path, source, {"errors": {"email": "Plain string"}})
    assert ">Plain string</p>" in html

    for errors in ({}, {"email": []}, {"other": ["x"]}):
        html = render(tmp_path, source, {"errors": errors})
        assert "text-red-600" not in html
        assert "border-gray-300" in html


def test_pagination_links_go_through_safe_url(tmp_path):
    class Result:
        has_more_pages = True
        current_page = 1

        def links(self, base_url):
            return [
                {"type": "prev", "disabled": False, "url": "javascript:alert(1)"},
                {"type": "page", "active": False, "url": "/p?page=2", "label": "2"},
                {"type": "next", "disabled": False, "url": "/p?page=2"},
            ]

    source = '{% from "pagination.html" import render_links %}{{ render_links(result, "/p") }}'
    html = render(tmp_path, source, {"result": Result()})
    assert "javascript:" not in html
    assert html.count('href="/p?page=2"') == 2
    assert 'href="#"' in html
