from hunt.view.directives import (
    _parse_component_open,
    _props_to_with_args,
    _split_by_sep,
    preprocess,
)


def test_extends():
    result = preprocess("@extends('layouts.app')")
    assert "{% extends 'layouts/app.html' %}" in result


def test_section_endsection():
    source = "@section('title')\nHello\n@endsection"
    result = preprocess(source)
    assert "{% block title %}" in result
    assert "{% endblock %}" in result


def test_yield():
    result = preprocess("@yield('content')")
    assert "{% block content %}" in result


def test_foreach():
    source = "@foreach($items as $item)\n{{ $item }}\n@endforeach"
    result = preprocess(source)
    assert "{% for item in items %}" in result
    assert "{% endfor %}" in result


def test_if_endif():
    source = "@if($user)\nHello\n@endif"
    result = preprocess(source)
    assert "{% if user %}" in result
    assert "{% endif %}" in result


def test_variable_echo():
    result = preprocess("{{ $name }}")
    assert "{{ name }}" in result


def test_csrf():
    result = preprocess("@csrf")
    assert 'type="hidden"' in result
    assert "_token" in result


def test_include():
    result = preprocess("@include('partials.nav')")
    assert "{% include 'partials/nav.html' %}" in result


# ---------------------------------------------------------------------------
# @component directive (M21)
# ---------------------------------------------------------------------------


class TestSplitBySep:
    def test_simple(self):
        assert _split_by_sep("a,b,c", ",") == ["a", "b", "c"]

    def test_respects_nested_brackets(self):
        assert _split_by_sep("a,[b,c],d", ",") == ["a", "[b,c]", "d"]

    def test_respects_strings(self):
        assert _split_by_sep("'a,b',c", ",") == ["'a,b'", "c"]

    def test_maxsplit(self):
        assert _split_by_sep("a:b:c", ":", maxsplit=1) == ["a", "b:c"]


class TestPropsToWithArgs:
    def test_empty_string(self):
        assert _props_to_with_args("") == ""

    def test_simple_dict(self):
        result = _props_to_with_args("{'type': 'success', 'message': msg}")
        assert "type='success'" in result
        assert "message=msg" in result

    def test_php_array_syntax(self):
        result = _props_to_with_args("['type' => 'success']")
        assert "type='success'" in result

    def test_nested_list_value(self):
        result = _props_to_with_args("{'headers': ['A', 'B'], 'rows': data}")
        assert "headers=['A', 'B']" in result
        assert "rows=data" in result

    def test_double_quoted_keys(self):
        result = _props_to_with_args('{"label": "Submit"}')
        # Jinja2 accepts both quote styles; verify key=value shape
        assert "label=" in result
        assert "Submit" in result


class TestParseComponentOpen:
    def test_name_only(self):
        name, args = _parse_component_open("'card'")
        assert name == "card"
        assert args == ""

    def test_name_with_props(self):
        name, args = _parse_component_open("'alert', {'type': 'success', 'message': msg}")
        assert name == "alert"
        assert "type='success'" in args
        assert "message=msg" in args


class TestComponentDirective:
    def test_self_closing_no_props(self):
        result = preprocess("@component('card')")
        assert "{% include 'components/card.html' %}" in result

    def test_self_closing_with_props(self):
        result = preprocess("@component('alert', {'type': 'success', 'message': msg})")
        assert "{% with type='success', message=msg %}" in result
        assert "{% include 'components/alert.html' %}" in result
        assert "{% endwith %}" in result

    def test_php_array_props(self):
        result = preprocess("@component('badge', ['color' => 'red', 'text' => label])")
        assert "{% with color='red', text=label %}" in result
        assert "{% include 'components/badge.html' %}" in result

    def test_block_form_no_slots(self):
        source = "@component('card', {'title': 'Hi'})\n    Body\n@endcomponent"
        result = preprocess(source)
        assert "{% set _slot_default %}Body{% endset %}" in result
        assert "{% include 'components/card.html' %}" in result

    def test_block_form_with_named_slot(self):
        source = (
            "@component('modal', {'title': 'Confirm'})\n"
            "    @slot('footer')<button>OK</button>@endslot\n"
            "    Are you sure?\n"
            "@endcomponent"
        )
        result = preprocess(source)
        assert "{% set _slot_footer %}<button>OK</button>{% endset %}" in result
        assert "{% set _slot_default %}Are you sure?{% endset %}" in result
        assert "{% include 'components/modal.html' %}" in result

    def test_component_name_with_hyphen(self):
        result = preprocess("@component('empty-state', {'title': 'Nothing'})")
        assert "{% include 'components/empty-state.html' %}" in result

    def test_multiple_components(self):
        source = "@component('badge', {'text': 'A'})\n@component('badge', {'text': 'B'})"
        result = preprocess(source)
        assert result.count("components/badge.html") == 2


class TestMakeComponent:
    def test_creates_file(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.make.component import make_component_command

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(make_component_command, ["UserCard"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "resources" / "views" / "components" / "user-card.html").exists()

    def test_slug_name(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.make.component import make_component_command

        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(make_component_command, ["MyButton"])
        assert (tmp_path / "resources" / "views" / "components" / "my-button.html").exists()

    def test_no_overwrite(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.make.component import make_component_command

        monkeypatch.chdir(tmp_path)
        out = tmp_path / "resources" / "views" / "components" / "alert.html"
        out.parent.mkdir(parents=True)
        out.write_text("# original\n")
        result = CliRunner().invoke(make_component_command, ["alert"])
        assert "Already exists" in result.output
        assert out.read_text() == "# original\n"


class TestVendorPublish:
    def test_publishes_components(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.vendor_publish import vendor_publish_command

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(vendor_publish_command, ["--tag", "components"])
        assert result.exit_code == 0, result.output
        dest = tmp_path / "resources" / "views" / "components"
        assert dest.is_dir()
        assert any(dest.glob("*.html"))

    def test_publishes_all_builtin_components(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.vendor_publish import vendor_publish_command

        monkeypatch.chdir(tmp_path)
        CliRunner().invoke(vendor_publish_command, ["--tag", "components"])
        dest = tmp_path / "resources" / "views" / "components"
        published = {f.name for f in dest.glob("*.html")}
        for name in ("alert.html", "badge.html", "button.html", "card.html",
                     "table.html", "modal.html", "navbar.html", "sidebar.html",
                     "empty-state.html", "form-group.html"):
            assert name in published, f"{name} not published"

    def test_skips_existing_without_force(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.vendor_publish import vendor_publish_command

        monkeypatch.chdir(tmp_path)
        dest = tmp_path / "resources" / "views" / "components" / "alert.html"
        dest.parent.mkdir(parents=True)
        dest.write_text("# custom\n")
        CliRunner().invoke(vendor_publish_command, ["--tag", "components"])
        assert dest.read_text() == "# custom\n"

    def test_force_overwrites_existing(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.vendor_publish import vendor_publish_command

        monkeypatch.chdir(tmp_path)
        dest = tmp_path / "resources" / "views" / "components" / "alert.html"
        dest.parent.mkdir(parents=True)
        dest.write_text("# custom\n")
        CliRunner().invoke(vendor_publish_command, ["--tag", "components", "--force"])
        assert dest.read_text() != "# custom\n"

    def test_unknown_tag_exits(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from hunt.console.commands.vendor_publish import vendor_publish_command

        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(vendor_publish_command, ["--tag", "bogus"])
        assert result.exit_code != 0
