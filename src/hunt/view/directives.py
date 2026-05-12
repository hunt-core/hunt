from __future__ import annotations

import re


def _find_matching_paren(text: str, start: int) -> int:
    """Given the index of '(', return the index just after the matching ')'."""
    depth = 0
    i = start
    in_string: str | None = None
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < len(text):
                i += 2
                continue
            if ch == in_string:
                in_string = None
        elif ch in ('"', "'"):
            in_string = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _replace_paren_directive(source: str, directive: str, transform) -> str:
    """Replace @directive(...) with correctly balanced paren matching."""
    pattern = f"@{directive}("
    result: list[str] = []
    i = 0
    while i < len(source):
        idx = source.find(pattern, i)
        if idx == -1:
            result.append(source[i:])
            break
        result.append(source[i:idx])
        paren_start = idx + len(pattern) - 1  # index of '('
        paren_end = _find_matching_paren(source, paren_start)
        inner = source[paren_start + 1 : paren_end - 1]
        result.append(transform(inner))
        i = paren_end
    return "".join(result)


def preprocess(source: str) -> str:
    """Transform hunt @-directives into Jinja2 syntax."""
    source = _strip_dollar_vars(source)
    source = _raw_echo(source)
    source = _extends(source)
    source = _sections(source)
    source = _yield(source)
    source = _include(source)
    source = _foreach(source)
    source = _for(source)
    source = _while(source)
    source = _conditionals(source)
    source = _csrf(source)
    source = _errors(source)
    source = _comments(source)
    source = _auth(source)
    source = _guest(source)
    source = _stack(source)
    source = _push(source)
    source = _component(source)
    return source


# ------------------------------------------------------------------
# Variable echo: {{ $var }} → {{ var }}
# ------------------------------------------------------------------

def _strip_dollar_vars(source: str) -> str:
    return re.sub(r"\{\{\s*\$(\w+)\s*\}\}", r"{{ \1 }}", source)


# ------------------------------------------------------------------
# Raw echo: {!! $var !!} → {{ var | safe }}
# ------------------------------------------------------------------

def _raw_echo(source: str) -> str:
    return re.sub(r"\{!!\s*\$?(\w+)\s*!!\}", r"{{ \1 | safe }}", source)


# ------------------------------------------------------------------
# @extends
# ------------------------------------------------------------------

def _extends(source: str) -> str:
    def repl(m: re.Match) -> str:
        tmpl = m.group(1).strip("'\"").replace(".", "/") + ".html"
        return f"{{% extends '{tmpl}' %}}"
    return re.sub(r"@extends\(\s*(['\"][\w./]+['\"])\s*\)", repl, source)


# ------------------------------------------------------------------
# @section / @endsection / @show
# ------------------------------------------------------------------

def _sections(source: str) -> str:
    source = re.sub(
        r"@section\(\s*['\"](\w+)['\"]\s*\)",
        r"{% block \1 %}",
        source,
    )
    source = re.sub(
        r"@section\(\s*['\"](\w+)['\"]\s*,\s*['\"]([^'\"]*)['\"]s*\)",
        r"{% block \1 %}\2{% endblock %}",
        source,
    )
    source = source.replace("@endsection", "{% endblock %}")
    source = source.replace("@stop", "{% endblock %}")
    source = source.replace("@show", "{% endblock %}")
    return source


# ------------------------------------------------------------------
# @yield
# ------------------------------------------------------------------

def _yield(source: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1).strip("'\"")
        default = m.group(2)
        if default:
            default = default.strip().strip("'\"")
            return f"{{% block {name} %}}{default}{{% endblock %}}"
        return f"{{% block {name} %}}{{% endblock %}}"
    return re.sub(r"@yield\(\s*(['\"\w]+)\s*(?:,\s*(['\"][^'\"]*['\"])\s*)?\)", repl, source)


# ------------------------------------------------------------------
# @include
# ------------------------------------------------------------------

def _include(source: str) -> str:
    def repl(m: re.Match) -> str:
        tmpl = m.group(1).strip("'\"").replace(".", "/") + ".html"
        return f"{{% include '{tmpl}' %}}"
    return re.sub(r"@include\(\s*(['\"][\w./]+['\"])\s*\)", repl, source)


# ------------------------------------------------------------------
# @foreach / @endforeach
# ------------------------------------------------------------------

def _foreach(source: str) -> str:
    def repl(m: re.Match) -> str:
        collection = m.group(1).lstrip("$")
        variable = m.group(2).lstrip("$")
        return f"{{% for {variable} in {collection} %}}"
    source = re.sub(r"@foreach\(\s*\$?([\w.]+)\s+as\s+\$?(\w+)\s*\)", repl, source)
    source = source.replace("@endforeach", "{% endfor %}")
    source = source.replace("@empty", "{% else %}")
    return source


# ------------------------------------------------------------------
# @for / @endfor
# ------------------------------------------------------------------

def _for(source: str) -> str:
    def repl(m: re.Match) -> str:
        var = m.group(1).lstrip("$")
        start = m.group(2)
        end = m.group(3)
        step = m.group(4) or "1"
        return f"{{% for {var} in range({start}, {end} + 1, {step}) %}}"
    source = re.sub(
        r"@for\(\s*\$?(\w+)\s*=\s*(\d+)\s*;\s*\$?\w+\s*<=?\s*(\d+)\s*;\s*\$?\w+\+\+(?:\s*\+\s*(\d+))?\s*\)",
        repl, source,
    )
    source = source.replace("@endfor", "{% endfor %}")
    return source


# ------------------------------------------------------------------
# @while / @endwhile
# ------------------------------------------------------------------

def _while(source: str) -> str:
    source = _replace_paren_directive(source, "while", lambda c: f"{{% while {c} %}}")
    source = source.replace("@endwhile", "{% endwhile %}")
    return source


# ------------------------------------------------------------------
# @if / @elseif / @else / @endif / @unless
# ------------------------------------------------------------------

def _conditionals(source: str) -> str:
    source = _replace_paren_directive(
        source, "if", lambda c: f"{{% if {c.lstrip('$')} %}}"
    )
    source = _replace_paren_directive(
        source, "elseif", lambda c: f"{{% elif {c.lstrip('$')} %}}"
    )
    source = source.replace("@else", "{% else %}")
    source = source.replace("@endif", "{% endif %}")
    source = _replace_paren_directive(
        source, "unless", lambda c: f"{{% if not {c.lstrip('$')} %}}"
    )
    source = source.replace("@endunless", "{% endif %}")
    return source


# ------------------------------------------------------------------
# @csrf
# ------------------------------------------------------------------

def _csrf(source: str) -> str:
    return source.replace(
        "@csrf",
        '<input type="hidden" name="_token" value="{{ csrf_token }}">',
    )


# ------------------------------------------------------------------
# @error / @enderror
# ------------------------------------------------------------------

def _errors(source: str) -> str:
    source = re.sub(
        r"@error\(\s*['\"](\w+)['\"]\s*\)",
        r"{% if errors is defined and '\1' in errors %}",
        source,
    )
    source = source.replace("@enderror", "{% endif %}")
    return source


# ------------------------------------------------------------------
# {{-- comments --}}
# ------------------------------------------------------------------

def _comments(source: str) -> str:
    return re.sub(r"\{\{--.*?--\}\}", "", source, flags=re.DOTALL)


# ------------------------------------------------------------------
# @auth / @endauth / @guest / @endguest
# ------------------------------------------------------------------

def _auth(source: str) -> str:
    source = source.replace("@auth", "{% if auth_user is defined and auth_user %}")
    source = source.replace("@endauth", "{% endif %}")
    return source


def _guest(source: str) -> str:
    source = source.replace("@guest", "{% if auth_user is not defined or not auth_user %}")
    source = source.replace("@endguest", "{% endif %}")
    return source


# ------------------------------------------------------------------
# @stack / @push / @endpush
# ------------------------------------------------------------------

def _stack(source: str) -> str:
    return re.sub(r"@stack\(\s*['\"](\w+)['\"]\s*\)", r"{{ _stacks.get('\1', '') | safe }}", source)


def _push(source: str) -> str:
    source = re.sub(r"@push\(\s*['\"](\w+)['\"]\s*\)", r"{% set _push_\1 %}", source)
    source = source.replace("@endpush", "{% endset %}")
    return source


# ------------------------------------------------------------------
# @component / @slot / @endslot / @endcomponent
# ------------------------------------------------------------------

def _component(source: str) -> str:
    def repl(m: re.Match) -> str:
        tmpl = m.group(1).strip("'\"").replace(".", "/") + ".html"
        return f"{{% include '{tmpl}' %}}"
    source = re.sub(r"@component\(\s*(['\"][\w./]+['\"])[^)]*\)", repl, source)
    source = source.replace("@endcomponent", "")
    source = re.sub(r"@slot\([^)]*\)", "", source)
    source = source.replace("@endslot", "")
    return source
