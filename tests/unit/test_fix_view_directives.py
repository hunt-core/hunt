"""VIEW-02: directive preprocessing. Every test renders through ViewFactory."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, TemplateSyntaxError

from hunt.view.directives import preprocess
from hunt.view.factory import ViewFactory, register_view_helpers

SRC = Path(__file__).resolve().parents[2] / "src" / "hunt"


def make_factory(tmp_path: Path, files: dict[str, str], **kwargs) -> ViewFactory:
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return ViewFactory(tmp_path, **kwargs)


def render(tmp_path: Path, source: str, data: dict | None = None, extra: dict[str, str] | None = None) -> str:
    factory = make_factory(tmp_path, {"page.html": source, **(extra or {})})
    return factory.make("page", data or {}).render()


def squash(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Whole-token matching
# ---------------------------------------------------------------------------


class TestTokenMatching:
    def test_email_addresses_are_left_alone(self, tmp_path):
        src = (
            "<p>admin@authority.com support@showcase.io me@stop.dev x@else.org "
            "a@empty.io b@guest.net c@csrf.com d@errors.org e@endif.com</p>"
        )
        assert render(tmp_path, src) == src

    def test_email_inside_open_blocks(self, tmp_path):
        src = "@section('c')@if(show)mail bob@endif.com or sue@else.org now@endif @endsection"
        out = render(tmp_path, src, {"show": True})
        assert "bob@endif.com" in out
        assert "sue@else.org" in out

    def test_directive_names_inside_longer_words(self, tmp_path):
        src = "@authority @guestbook @elsewhere @csrf_field @showcase @errorsx @endifx"
        assert render(tmp_path, src) == src

    def test_alpine_and_css_at_rules_pass_through(self, tmp_path):
        src = (
            '<button @click="open = !open" @click.away="open = false" @keydown.escape.window="x()">b</button>\n'
            '<img @error="broken = true">\n'
            "<style>\n"
            "@import url('a.css');\n"
            "@media (min-width: 640px) { .a { color: red } }\n"
            "@keyframes spin { from { top: 0 } to { top: 1px } }\n"
            "@font-face { font-family: X }\n"
            "@supports (display: grid) { .b { display: grid } }\n"
            "</style>"
        )
        assert render(tmp_path, src) == src

    def test_double_at_escapes_a_directive(self, tmp_path):
        out = render(tmp_path, "@@if(x) @@csrf @@click @@endsection")
        assert out == "@if(x) @csrf @click @endsection"

    def test_directive_requiring_args_without_parens_is_text(self, tmp_path):
        src = "mention @if or @foreach or @include in prose"
        assert render(tmp_path, src) == src

    def test_compact_closers_still_work(self, tmp_path):
        assert render(tmp_path, "@if($x)yes@else no@endif", {"x": True}) == "yes"
        assert render(tmp_path, "@if($x)yes@else no@endif", {"x": False}) == " no"
        assert render(tmp_path, "@foreach($xs as $x){{ $x }}@endforeach", {"xs": [1, 2]}) == "12"

    def test_nothing_is_matched_inside_jinja_regions(self, tmp_path):
        src = "{{ '@csrf' }}|{% set a = '@auth' %}{{ a }}|{# @if( #}|{% raw %}@csrf {{ $x }}{% endraw %}"
        assert render(tmp_path, src) == "@csrf|@auth||@csrf {{ $x }}"

    def test_verbatim(self, tmp_path):
        out = render(tmp_path, "@verbatim{{ $raw }} @if(a) @endif@endverbatim{{ $n }}", {"n": 1})
        assert out == "{{ $raw }} @if(a) @endif1"

    def test_comment_removed(self, tmp_path):
        assert render(tmp_path, "a{{-- gone\n@if( --}}b") == "ab"


# ---------------------------------------------------------------------------
# Individual directives
# ---------------------------------------------------------------------------


class TestConditionals:
    def test_if_elseif_else(self, tmp_path):
        src = "@if($n == 1)one@elseif($n == 2)two@else many@endif"
        factory = make_factory(tmp_path, {"page.html": src})
        assert factory.make("page", {"n": 1}).render() == "one"
        assert factory.make("page", {"n": 2}).render() == "two"
        assert factory.make("page", {"n": 3}).render() == " many"

    def test_if_with_space_and_nested_parens(self, tmp_path):
        assert render(tmp_path, "@if ((a or b) and fn(')'))ok@endif", {"a": 0, "b": 1, "fn": bool}) == "ok"

    def test_unless(self, tmp_path):
        src = "@unless($a or $b)none@endunless"
        assert render(tmp_path, src, {"a": False, "b": False}) == "none"
        assert render(tmp_path, src, {"a": False, "b": True}) == ""

    def test_auth_and_guest(self, tmp_path):
        src = "@auth\nin\n@endauth\n@guest\nout\n@endguest"
        factory = make_factory(tmp_path, {"page.html": src})
        assert factory.make("page", {"auth_user": {"id": 1}}).render().split() == ["in"]
        assert factory.make("page", {"auth_user": None}).render().split() == ["out"]

    def test_can(self, tmp_path):
        src = "@can('edit', post)\nE\n@endcan"
        factory = make_factory(tmp_path, {"page.html": src})
        seen = []

        def can(ability, *args):
            seen.append((ability, args))
            return ability == "edit"

        assert factory.make("page", {"can": can, "post": 7}).render().strip() == "E"
        assert seen == [("edit", (7,))]

    def test_cannot_renders_without_stray_text(self, tmp_path):
        """@endcannot used to be half-eaten by @endcan, leaving a literal 'not'."""
        src = "<i>@cannot('delete')\n<p>Not allowed</p>\n@endcannot</i>"
        factory = make_factory(tmp_path, {"page.html": src})
        denied = factory.make("page", {"can": lambda *a: False}).render()
        allowed = factory.make("page", {"can": lambda *a: True}).render()
        assert squash(denied) == "<i> <p>Not allowed</p> </i>"
        assert allowed == "<i></i>"

    def test_env(self, tmp_path):
        src = "@env('local')L@endenv @env(['production', 'staging'])P@endenv"
        factory = make_factory(tmp_path, {"page.html": src})
        assert factory.make("page", {"_app_env": "local"}).render().strip() == "L"
        assert factory.make("page", {"_app_env": "staging"}).render().strip() == "P"

    def test_switch(self, tmp_path):
        src = "@switch($s)\n@case(1)\nOne\n@break\n@case('b')\nBee\n@break\n@default\nOther\n@endswitch"
        factory = make_factory(tmp_path, {"page.html": src})
        assert factory.make("page", {"s": 1}).render().split() == ["One"]
        assert factory.make("page", {"s": "b"}).render().split() == ["Bee"]
        assert factory.make("page", {"s": 9}).render().split() == ["Other"]

    def test_nested_switch(self, tmp_path):
        src = "@switch($a)@case(1)@switch($b)@case(1)A1B1@break@default A1Bx@endswitch@break@default Ax@endswitch"
        factory = make_factory(tmp_path, {"page.html": src})
        assert factory.make("page", {"a": 1, "b": 1}).render() == "A1B1"
        assert factory.make("page", {"a": 1, "b": 2}).render() == " A1Bx"
        assert factory.make("page", {"a": 2, "b": 1}).render() == " Ax"

    def test_break_and_default_outside_switch_are_text(self, tmp_path):
        assert render(tmp_path, "@break @default @case(1)") == "@break @default @case(1)"


class TestLoops:
    def test_foreach_dotted_collection(self, tmp_path):
        out = render(
            tmp_path,
            "@foreach($user.posts as $post)[{{ $post.title }}]@endforeach",
            {"user": {"posts": [{"title": "a"}, {"title": "b"}]}},
        )
        assert out == "[a][b]"

    def test_foreach_key_value(self, tmp_path):
        out = render(tmp_path, "@foreach($map as $k => $v){{ $k }}={{ $v }};@endforeach", {"map": {"a": 1, "b": 2}})
        assert out == "a=1;b=2;"

    def test_foreach_call_expression(self, tmp_path):
        assert render(tmp_path, "@foreach(range(1, 4) as $i){{ $i }}@endforeach") == "123"

    def test_forelse(self, tmp_path):
        src = "@forelse($items as $item){{ $item }}@empty none @endforelse"
        factory = make_factory(tmp_path, {"page.html": src})
        assert factory.make("page", {"items": [1, 2]}).render() == "12"
        assert factory.make("page", {"items": []}).render() == " none "

    def test_for_exclusive_upper_bound(self, tmp_path):
        assert render(tmp_path, "@for($i = 0; $i < 3; $i++){{ $i }}@endfor") == "012"

    def test_for_inclusive_upper_bound(self, tmp_path):
        assert render(tmp_path, "@for($i = 1; $i <= 3; $i++){{ $i }}@endfor") == "123"

    def test_for_step_variable_bound_and_countdown(self, tmp_path):
        assert render(tmp_path, "@for($i = 0; $i < $n; $i += 2){{ $i }}@endfor", {"n": 5}) == "024"
        assert render(tmp_path, "@for($i = 3; $i > 0; $i--){{ $i }}@endfor") == "321"
        assert render(tmp_path, "@for($i = 3; $i >= 0; $i--){{ $i }}@endfor") == "3210"

    def test_for_unparseable_raises_with_line(self, tmp_path):
        with pytest.raises(TemplateSyntaxError) as exc:
            preprocess("line1\n@for($i = 0)\nx\n@endfor")
        assert exc.value.lineno == 2
        assert "@for" in exc.value.message

    def test_while_is_a_clear_compile_error(self, tmp_path):
        factory = make_factory(tmp_path, {"page.html": "a\nb\n@while($x)\nloop\n@endwhile"})
        with pytest.raises(TemplateSyntaxError) as exc:
            factory.make("page")
        assert "@while" in exc.value.message
        assert exc.value.lineno == 3
        assert str(exc.value.filename).endswith("page.html")


class TestEcho:
    def test_dollar_attribute_access(self, tmp_path):
        assert (
            render(tmp_path, "{{ $user.name }} {{ $user['name'] }}", {"user": {"name": "<b>"}}) == "&lt;b&gt; &lt;b&gt;"
        )

    def test_dollar_inside_string_is_kept(self, tmp_path):
        assert render(tmp_path, "{{ '$price' }} {{ $a + $b }}", {"a": 1, "b": 2}) == "$price 3"

    def test_raw_echo(self, tmp_path):
        data = {"x": {"y": "<b>hi</b>"}, "s": "<i>"}
        assert render(tmp_path, "{!! $x.y !!}{!! $s !!}{!! s ~ s !!}", data) == "<b>hi</b><i><i><i>"

    def test_json(self, tmp_path):
        assert render(tmp_path, "@json($data) @json($d.k, FLAGS)", {"data": [1], "d": {"k": "a"}}) == '[1] "a"'


class TestForms:
    def test_csrf_and_method(self, tmp_path):
        out = render(tmp_path, "@csrf @method('put') @method(\"DELETE\")", {"csrf_token": "tok<"})
        assert out == (
            '<input type="hidden" name="_token" value="tok&lt;"> '
            '<input type="hidden" name="_method" value="PUT"> '
            '<input type="hidden" name="_method" value="DELETE">'
        )

    def test_method_expression_is_escaped(self, tmp_path):
        out = render(tmp_path, "@method($verb)", {"verb": '"><script>'})
        assert out == '<input type="hidden" name="_method" value="&#34;&gt;&lt;script&gt;">'

    def test_method_rejects_garbage(self):
        with pytest.raises(TemplateSyntaxError):
            preprocess("@method('P UT')")

    def test_error_block_and_errors_summary(self, tmp_path):
        src = "@error('title')<b>bad</b>@enderror|@error('body')<b>body</b>@enderror|@errors"
        out = render(tmp_path, src, {"errors": {"title": ["Too <short>"]}})
        assert out.startswith("<b>bad</b>||<div")
        assert "<li>Too &lt;short&gt;</li>" in out

    def test_old(self, tmp_path):
        src = "<input value=\"@old('title')\"><input value=\"@old('missing', 'dflt')\">"
        out = render(tmp_path, src, {"old": lambda key, default="": {"title": 'a"b'}.get(key, default)})
        assert out == '<input value="a&#34;b"><input value="dflt">'


# ---------------------------------------------------------------------------
# Layouts and includes
# ---------------------------------------------------------------------------


class TestLayouts:
    def test_extends_section_yield_show(self, tmp_path):
        files = {
            "layouts/app.html": (
                "<title>@yield('title', 'Default')</title>"
                "@section('sidebar')base-side@show"
                "<main>@yield('content')</main>"
                "@yield('page-footer')"
            ),
            "page.html": (
                "@extends('layouts.app')\n"
                "@section('title', 'Home')\n"
                "@section('content')\n<p>{{ $msg }}</p>\n@endsection\n"
                "@section('page-footer')F@stop\n"
            ),
            "plain.html": "@extends('layouts.app')",
        }
        factory = make_factory(tmp_path, files)
        out = factory.make("page", {"msg": "hi"}).render()
        assert squash(out) == "<title>Home</title>base-side<main> <p>hi</p> </main>F"
        assert factory.make("plain").render() == "<title>Default</title>base-side<main></main>"

    def test_section_inline_value_with_whitespace_before_paren(self, tmp_path):
        """The old regex had a `s*` typo, so `'x' )` was not matched."""
        files = {
            "l.html": "[@yield('title')]",
            "page.html": "@extends('l')\n@section('title', 'spaced' )\n",
            "expr.html": "@extends('l')\n@section('title', $t)\n",
        }
        factory = make_factory(tmp_path, files)
        assert factory.make("page").render() == "[spaced]"
        assert factory.make("expr", {"t": "<x>"}).render() == "[&lt;x&gt;]"

    def test_include_hyphenated_name(self, tmp_path):
        out = render(
            tmp_path,
            "<@include('partials.nav-bar')>",
            extra={"partials/nav-bar.html": "NAV {{ who }}"},
            data={"who": "me"},
        )
        assert out == "<NAV me>"

    def test_include_with_data(self, tmp_path):
        extra = {"partials/item.html": "[{{ label }}:{{ n }}]"}
        src = "@include('partials.item', {'label': 'a', 'n': count + 1})@include('partials.item', ['label' => $x])"
        out = render(tmp_path, src, {"count": 1, "x": "b", "n": 9, "label": "outer"}, extra)
        assert out == "[a:2][b:9]"

    def test_include_data_does_not_leak(self, tmp_path):
        extra = {"p.html": "{{ v }}"}
        assert render(tmp_path, "@include('p', {'v': 'in'})|{{ v|default('unset') }}", extra=extra) == "in|unset"

    def test_include_non_literal_data_is_an_error(self):
        with pytest.raises(TemplateSyntaxError) as exc:
            preprocess("\n\n@include('p', data)")
        assert exc.value.lineno == 3

    def test_include_if_when_first(self, tmp_path):
        extra = {"a.html": "A{{ v|default('') }}", "b/c.html": "BC"}
        src = (
            "@includeIf('missing.view')@includeIf('a', {'v': 1})|"
            "@includeWhen($yes, 'a')@includeWhen($no, 'a')@includeWhen($yes, 'b.c', {'z': 1})|"
            "@includeFirst(['nope', 'b.c', 'a'])@includeFirst(['a'], {'v': 2})"
        )
        assert render(tmp_path, src, {"yes": True, "no": False}, extra) == "A1|ABC|BCA2"


# ---------------------------------------------------------------------------
# Stacks
# ---------------------------------------------------------------------------


class TestStacks:
    LAYOUT = "<head>@stack('styles')</head><body>@yield('content')@stack('scripts')</body>"

    def test_push_from_section_renders_in_parent_stack(self, tmp_path):
        files = {
            "layout.html": self.LAYOUT,
            "page.html": (
                "@extends('layout')\n"
                "@section('content')\n"
                "<p>body</p>\n"
                "@push('styles')<link href=\"a.css\">@endpush\n"
                "@push('scripts')<script>one()</script>@endpush\n"
                "@push('scripts')<script>two()</script>@endpush\n"
                "@prepend('scripts')<script>zero()</script>@endprepend\n"
                "@endsection\n"
            ),
        }
        out = make_factory(tmp_path, files).make("page").render()
        assert squash(out) == (
            '<head><link href="a.css"></head><body> <p>body</p> '
            "<script>zero()</script><script>one()</script><script>two()</script></body>"
        )

    def test_push_at_top_level_of_child_template(self, tmp_path):
        files = {
            "layout.html": self.LAYOUT,
            "page.html": (
                "@extends('layout')\n"
                "@push('scripts')<script src=\"{{ $src }}\"></script>@endpush\n"
                "@section('content')C@endsection\n"
            ),
        }
        out = make_factory(tmp_path, files).make("page", {"src": "/a.js?x=1&y=2"}).render()
        assert out == '<head></head><body>C<script src="/a.js?x=1&amp;y=2"></script></body>'

    def test_stack_before_push_in_same_template_and_from_include(self, tmp_path):
        files = {
            "page.html": "[@stack('s')]@push('s')A@endpush @include('part') [@stack('s')][@stack('empty')]",
            "part.html": "@push('s')B@endpush",
        }
        assert make_factory(tmp_path, files).make("page").render() == "[AB]  [AB][]"

    def test_pushed_variables_are_escaped(self, tmp_path):
        out = render(tmp_path, "@push('s'){{ $v }}@endpush@stack('s')", {"v": "<script>"})
        assert out == "&lt;script&gt;"

    def test_each_render_starts_with_empty_stacks(self, tmp_path):
        factory = make_factory(tmp_path, {"page.html": "@push('s')x@endpush@stack('s')"})
        view = factory.make("page")
        assert view.render() == "x"
        assert view.render() == "x"
        assert str(factory.make("page")) == "x"

    def test_unclosed_push_is_an_error(self):
        with pytest.raises(TemplateSyntaxError) as exc:
            preprocess("a\n@push('s')\nx")
        assert exc.value.lineno == 2
        with pytest.raises(TemplateSyntaxError):
            preprocess("x @endpush")


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

BOX = "<box t='{{ title|default('') }}'>{{ _slot_default|default('') }}|{{ _slot_footer|default('') }}</box>"
TAG = "<tag>{{ text|default('') }}{{ _slot_default|default('') }}</tag>"


class TestComponents:
    def render(self, tmp_path, source, data=None):
        extra = {"components/box.html": BOX, "components/tag.html": TAG, "components/ui/chip.html": "<chip/>"}
        return squash(render(tmp_path, source, data, extra))

    def test_self_closing_followed_by_block(self, tmp_path):
        src = "@component('tag', {'text': 'A'})\n<hr>\n@component('box', {'title': 'T'})\nbody\n@endcomponent\nafter"
        assert self.render(tmp_path, src) == "<tag>A</tag> <hr> <box t='T'>body|</box> after"

    def test_nested_block_components(self, tmp_path):
        src = (
            "@component('box', {'title': 'outer'})\n"
            "  before\n"
            "  @component('box', {'title': 'inner'})\n"
            "    deep\n"
            "  @endcomponent\n"
            "  after\n"
            "@endcomponent"
        )
        assert self.render(tmp_path, src) == "<box t='outer'>before <box t='inner'>deep|</box> after|</box>"

    def test_block_containing_only_a_self_closing_component(self, tmp_path):
        src = "@component('box', {'title': 'T'})\n  @component('tag', {'text': 'x'})\n@endcomponent"
        assert self.render(tmp_path, src) == "<box t='T'><tag>x</tag>|</box>"

    def test_slots_do_not_leak_into_later_components(self, tmp_path):
        src = (
            "@component('box')@slot('footer')F1@endslot body1 @endcomponent\n"
            "@component('box')\n"
            "@component('box') body3 @endcomponent"
        )
        assert self.render(tmp_path, src) == "<box t=''>body1|F1</box> <box t=''>|</box> <box t=''>body3|</box>"

    def test_slots_do_not_leak_into_nested_components(self, tmp_path):
        src = (
            "@component('box', {'title': 'o'})"
            "@slot('footer')OF@endslot"
            "@component('box', {'title': 'i'}) inner @endcomponent"
            "@component('tag', {'text': 't'})"
            "@endcomponent"
        )
        assert self.render(tmp_path, src) == "<box t='o'><box t='i'>inner|</box><tag>t</tag>|OF</box>"

    def test_props_do_not_leak(self, tmp_path):
        src = "@component('box', {'title': 'T'})@endcomponent{{ title|default('none') }}"
        assert self.render(tmp_path, src) == "<box t='T'>|</box>none"

    def test_inline_slot_and_directives_inside_slots(self, tmp_path):
        src = "@component('box')@slot('footer', $foot)@if($on)@foreach($xs as $x){{ $x }}@endforeach@endif@endcomponent"
        assert self.render(tmp_path, src, {"foot": "<f>", "on": True, "xs": [1, 2]}) == "<box t=''>12|&lt;f&gt;</box>"

    def test_slot_content_is_markup_but_variables_in_it_are_escaped(self, tmp_path):
        src = "@component('box')<b>{{ $v }}</b>@endcomponent"
        assert self.render(tmp_path, src, {"v": "<i>"}) == "<box t=''><b>&lt;i&gt;</b>|</box>"

    def test_dotted_component_name_and_php_array_props(self, tmp_path):
        assert self.render(tmp_path, "@component('ui.chip')@component('tag', ['text' => $t])", {"t": "x"}) == (
            "<chip/><tag>x</tag>"
        )

    def test_stray_endcomponent_and_slot_errors(self):
        with pytest.raises(TemplateSyntaxError) as exc:
            preprocess("x\n@endcomponent")
        assert exc.value.lineno == 2
        with pytest.raises(TemplateSyntaxError):
            preprocess("@component('a')@slot('s')never closed@endcomponent")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestSyntaxErrors:
    def test_unbalanced_parentheses_report_the_line(self, tmp_path):
        src = "<p>one</p>\n<p>two</p>\n@if($a and ($b or $c)\n<p>rest of the file</p>\n@endif\n"
        with pytest.raises(TemplateSyntaxError) as exc:
            preprocess(src)
        assert exc.value.lineno == 3
        assert "@if" in exc.value.message
        factory = make_factory(tmp_path, {"page.html": src})
        with pytest.raises(TemplateSyntaxError) as exc:
            factory.make("page")
        assert exc.value.lineno == 3

    def test_unterminated_verbatim(self):
        with pytest.raises(TemplateSyntaxError):
            preprocess("@verbatim never closed")


# ---------------------------------------------------------------------------
# Framework templates
# ---------------------------------------------------------------------------


def _framework_templates() -> list[Path]:
    found = sorted((SRC / "views").rglob("*.html")) + sorted((SRC / "admin" / "templates").rglob("*.html"))
    assert found
    return found


@pytest.mark.parametrize("template", _framework_templates(), ids=lambda p: str(p.relative_to(SRC)))
def test_framework_template_preprocesses_and_compiles(template):
    env = Environment(autoescape=True, extensions=_admin_extensions())
    register_view_helpers(env)
    env.filters.update(dict.fromkeys(_admin_filter_names(), lambda value, *a, **k: value))
    source = template.read_text(encoding="utf-8")
    processed = preprocess(source)
    env.compile(processed, name=template.name, filename=str(template))
    if "admin" in template.relative_to(SRC).parts[:1]:
        # the admin panel uses plain Jinja: the preprocessor must not change those templates
        assert processed == source


def _admin_extensions() -> list[str]:
    return []


def _admin_filter_names() -> list[str]:
    """Custom filters used by the admin templates (registered by the admin app, not needed to compile)."""
    import re

    names: set[str] = set()
    for path in (SRC / "admin" / "templates").rglob("*.html"):
        names.update(re.findall(r"\|\s*([a-z_]+)", path.read_text(encoding="utf-8")))
    return sorted(names - set(Environment().filters))


def test_bundled_auth_views_render_through_view_factory(tmp_path):
    factory = ViewFactory(tmp_path)
    out = factory.make(
        "auth.login",
        {"status": "", "errors": {"email": ["Bad <creds>"]}, "csrf_token": "t", "config": lambda k, d=None: d},
    ).render()
    assert "<h1>Sign in</h1>" in out
    assert "Bad &lt;creds&gt;" in out
