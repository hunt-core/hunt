from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_instance(attrs: dict):
    inst = MagicMock()
    inst._attributes = attrs
    return inst


class TestMarkdownFieldMeta:
    def test_field_type(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        assert f.field_type == "markdown"

    def test_attribute_derived_from_name(self):
        from hunt.admin.fields.markdown import Markdown

        # Str.snake lowercases; underscores come from camelCase conversion
        f = Markdown("Body")
        assert f.attribute == "body"

    def test_attribute_explicit(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body", attribute="content")
        assert f.attribute == "content"

    def test_hidden_from_index_by_default(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        assert f._show_on_index is False

    def test_shown_on_detail_by_default(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        assert f._show_on_detail is True

    def test_shown_on_create_by_default(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        assert f._show_on_create is True

    def test_shown_on_edit_by_default(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        assert f._show_on_edit is True

    def test_fluent_rules(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body").rules("required")
        assert "required" in f._rules

    def test_fluent_readonly(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body").readonly()
        assert f._readonly is True

    def test_fluent_hide_from_detail(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body").hide_from_detail()
        assert f._show_on_detail is False

    def test_exported_from_fields_package(self):
        from hunt.admin.fields import Markdown  # noqa: F401


class TestMarkdownDisplayValue:
    def test_empty_attribute_returns_empty(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": None})
        assert f.display_value(inst) == ""

    def test_missing_attribute_returns_empty(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({})
        assert f.display_value(inst) == ""

    def test_strips_html_tags(self):
        from hunt.admin.fields.markdown import Markdown

        # display_value strips HTML tags from the raw stored value
        f = Markdown("Body")
        inst = _make_instance({"body": "<p>Hello <strong>world</strong></p>"})
        val = f.display_value(inst)
        assert "<p>" not in val
        assert "<strong>" not in val
        assert "Hello" in val
        assert "world" in val

    def test_truncates_at_200(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "a" * 300})
        val = f.display_value(inst)
        assert val.endswith("…")
        assert len(val) == 201  # 200 chars + ellipsis

    def test_no_truncation_under_200(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "hello world"})
        assert f.display_value(inst) == "hello world"


class TestMarkdownRenderHtml:
    def test_empty_returns_empty(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": ""})
        assert f.render_html(inst) == ""

    def test_none_returns_empty(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": None})
        assert f.render_html(inst) == ""

    def test_bold_rendered(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "**bold text**"})
        html = f.render_html(inst)
        assert "<strong>" in html
        assert "bold text" in html

    def test_italic_rendered(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "_italic text_"})
        html = f.render_html(inst)
        assert "<em>" in html
        assert "italic text" in html

    def test_heading_rendered(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "## Section"})
        html = f.render_html(inst)
        assert "<h2>" in html
        assert "Section" in html

    def test_table_rendered(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "| A | B |\n|---|---|\n| 1 | 2 |"})
        html = f.render_html(inst)
        assert "<table>" in html
        assert "<th>" in html
        assert "<td>" in html

    def test_image_rendered(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "![alt](https://example.com/img.png)"})
        html = f.render_html(inst)
        assert "<img" in html
        assert "src=" in html

    def test_link_rendered_with_rel(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": "[click](https://example.com)"})
        html = f.render_html(inst)
        assert "<a" in html
        assert "noopener" in html

    def test_script_tag_stripped(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": '<script>alert("xss")</script>'})
        html = f.render_html(inst)
        assert "<script>" not in html
        assert "alert" not in html

    def test_onclick_attribute_stripped(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = _make_instance({"body": '<a href="#" onclick="evil()">link</a>'})
        html = f.render_html(inst)
        assert "onclick" not in html

    def test_instance_without_attributes(self):
        from hunt.admin.fields.markdown import Markdown

        f = Markdown("Body")
        inst = MagicMock(spec=[])  # no _attributes
        assert f.render_html(inst) == ""


class TestRenderMarkdownFallbacks:
    def test_fallback_when_markdown_not_installed(self):
        from hunt.admin.fields import markdown as md_module

        with patch.dict("sys.modules", {"markdown": None}):
            result = md_module._render_markdown("hello **world**")
        # fallback escapes HTML and replaces newlines — should not raise
        assert "hello" in result

    def test_fallback_when_nh3_not_installed(self):
        from hunt.admin.fields import markdown as md_module

        with patch.dict("sys.modules", {"nh3": None}):
            result = md_module._render_markdown("**bold**")
        # nh3 fallback strips all tags — should not raise
        assert isinstance(result, str)
