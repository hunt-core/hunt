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
    # Protect @verbatim blocks before any other transforms touch {{ }}
    source, _vmap = _extract_verbatim(source)
    source = _strip_dollar_vars(source)
    source = _raw_echo(source)
    source = _json(source)
    source = _extends(source)
    source = _sections(source)
    source = _yield(source)
    source = _include_first(source)
    source = _include_when(source)
    source = _include_if(source)
    source = _include(source)
    source = _forelse(source)
    source = _foreach(source)
    source = _for(source)
    source = _while(source)
    source = _switch(source)
    source = _conditionals(source)
    source = _csrf(source)
    source = _errors(source)
    source = _comments(source)
    source = _auth(source)
    source = _guest(source)
    source = _env(source)
    source = _can(source)
    source = _cannot(source)
    source = _once(source)
    source = _py(source)
    source = _stack(source)
    source = _push(source)
    source = _prepend(source)
    source = _component(source)
    # Restore verbatim blocks last
    source = _restore_verbatim(source, _vmap)
    return source


# ------------------------------------------------------------------
# @verbatim / @endverbatim — protect content from preprocessing
# ------------------------------------------------------------------


def _extract_verbatim(source: str) -> tuple[str, dict[str, str]]:
    """Replace @verbatim...@endverbatim blocks with unique placeholders."""
    verbatim_map: dict[str, str] = {}
    counter = [0]

    def repl(m: re.Match) -> str:
        key = f"__HUNT_VERBATIM_{counter[0]}__"
        verbatim_map[key] = "{% raw %}" + m.group(1) + "{% endraw %}"
        counter[0] += 1
        return key

    source = re.sub(r"@verbatim(.*?)@endverbatim", repl, source, flags=re.DOTALL)
    return source, verbatim_map


def _restore_verbatim(source: str, verbatim_map: dict[str, str]) -> str:
    for key, value in verbatim_map.items():
        source = source.replace(key, value)
    return source


# ------------------------------------------------------------------
# Variable echo: {{ $var }} → {{ var }}
# ------------------------------------------------------------------


def _strip_dollar_vars(source: str) -> str:
    return re.sub(r"\{\{\s*\$(\w+)\s*\}\}", r"{{ \1 }}", source)


# ------------------------------------------------------------------
# Raw echo: {!! $var !!} → {{ var | safe }}
# WARNING: outputs unescaped HTML — never use with user-supplied data (XSS risk).
# ------------------------------------------------------------------


def _raw_echo(source: str) -> str:
    return re.sub(r"\{!!\s*\$?(\w+)\s*!!\}", r"{{ \1 | safe }}", source)


# ------------------------------------------------------------------
# @json($var) / @json($var, flags) → {{ var | tojson }}
# ------------------------------------------------------------------


def _json(source: str) -> str:
    def repl(inner: str) -> str:
        # Take first argument (the variable), strip $ and whitespace
        var = inner.split(",")[0].strip().lstrip("$")
        return f"{{{{ {var} | tojson }}}}"

    return _replace_paren_directive(source, "json", repl)


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
# @includeFirst(['view.a', 'view.b']) → {% include [...] ignore missing %}
# ------------------------------------------------------------------


def _include_first(source: str) -> str:
    def repl(inner: str) -> str:
        # inner looks like: ['view.one', 'view.two'] or 'view.one', 'view.two'
        names = re.findall(r"['\"]([^'\"]+)['\"]", inner.split("]")[0])
        paths = [n.replace(".", "/") + ".html" for n in names]
        path_list = "[" + ", ".join(f"'{p}'" for p in paths) + "]"
        return f"{{% include {path_list} ignore missing %}}"

    return _replace_paren_directive(source, "includeFirst", repl)


# ------------------------------------------------------------------
# @includeWhen($cond, 'view', [...]) → {% if cond %}{% include '...' %}{% endif %}
# ------------------------------------------------------------------


def _include_when(source: str) -> str:
    def repl(inner: str) -> str:
        # Split on first comma after closing paren/quote of condition
        # Simple approach: first token is condition, second is view name
        parts = re.split(r",\s*", inner, maxsplit=1)
        if len(parts) < 2:
            return "{# @includeWhen parse error #}"
        cond = parts[0].strip().lstrip("$")
        # Extract view name from rest
        view_match = re.search(r"['\"]([^'\"]+)['\"]", parts[1])
        if not view_match:
            return "{# @includeWhen parse error #}"
        tmpl = view_match.group(1).replace(".", "/") + ".html"
        return f"{{% if {cond} %}}{{% include '{tmpl}' %}}{{% endif %}}"

    return _replace_paren_directive(source, "includeWhen", repl)


# ------------------------------------------------------------------
# @includeIf('view.name') → {% include '...' ignore missing %}
# ------------------------------------------------------------------


def _include_if(source: str) -> str:
    def repl(inner: str) -> str:
        name_match = re.search(r"['\"]([^'\"]+)['\"]", inner)
        if not name_match:
            return "{# @includeIf parse error #}"
        tmpl = name_match.group(1).replace(".", "/") + ".html"
        return f"{{% include '{tmpl}' ignore missing %}}"

    return _replace_paren_directive(source, "includeIf", repl)


# ------------------------------------------------------------------
# @include
# ------------------------------------------------------------------


def _include(source: str) -> str:
    def repl(m: re.Match) -> str:
        tmpl = m.group(1).strip("'\"").replace(".", "/") + ".html"
        return f"{{% include '{tmpl}' %}}"

    return re.sub(r"@include\(\s*(['\"][\w./]+['\"])\s*\)", repl, source)


# ------------------------------------------------------------------
# @forelse / @empty / @endforelse
# ------------------------------------------------------------------


def _forelse(source: str) -> str:
    def repl(m: re.Match) -> str:
        collection = m.group(1).lstrip("$")
        variable = m.group(2).lstrip("$")
        return f"{{% for {variable} in {collection} %}}"

    source = re.sub(r"@forelse\(\s*\$?([\w.]+)\s+as\s+\$?(\w+)\s*\)", repl, source)
    source = source.replace("@endforelse", "{% endfor %}")
    return source


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
        repl,
        source,
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
# @switch / @case / @break / @default / @endswitch
# ------------------------------------------------------------------


def _switch(source: str) -> str:
    """Convert @switch / @case / @break / @default / @endswitch to if/elif/else."""
    result: list[str] = []
    pos = 0
    switch_re = re.compile(r"@switch\(")
    endswitch_re = re.compile(r"@endswitch")

    while pos < len(source):
        m = switch_re.search(source, pos)
        if not m:
            result.append(source[pos:])
            break

        result.append(source[pos : m.start()])

        paren_start = m.start() + len("@switch")
        paren_end = _find_matching_paren(source, paren_start)
        var = source[paren_start + 1 : paren_end - 1].strip().lstrip("$")

        em = endswitch_re.search(source, paren_end)
        if em is None:
            result.append(source[m.start() :])
            break

        body = source[paren_end : em.start()]
        result.append(_convert_switch_body(var, body))
        result.append("{% endif %}")
        pos = em.end()

    return "".join(result)


def _convert_switch_body(var: str, body: str) -> str:
    is_first = [True]

    def repl_case(cm: re.Match) -> str:
        val = cm.group(1).strip()
        if is_first[0]:
            is_first[0] = False
            return f"{{% if {var} == {val} %}}"
        return f"{{% elif {var} == {val} %}}"

    body = re.sub(r"@case\(([^)]+)\)", repl_case, body)
    body = re.sub(r"@break\b", "", body)
    body = re.sub(r"@default\b", "{% else %}", body)
    return body


# ------------------------------------------------------------------
# @if / @elseif / @else / @endif / @unless
# ------------------------------------------------------------------


def _conditionals(source: str) -> str:
    source = _replace_paren_directive(source, "if", lambda c: f"{{% if {c.lstrip('$')} %}}")
    source = _replace_paren_directive(source, "elseif", lambda c: f"{{% elif {c.lstrip('$')} %}}")
    source = source.replace("@else", "{% else %}")
    source = source.replace("@endif", "{% endif %}")
    source = _replace_paren_directive(source, "unless", lambda c: f"{{% if not {c.lstrip('$')} %}}")
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
# @env('production') / @endenv
# ------------------------------------------------------------------


def _env(source: str) -> str:
    """@env('production') or @env(['production', 'staging']) → if _app_env check."""

    def repl(inner: str) -> str:
        inner = inner.strip()
        # Array form: ['prod', 'staging']
        if inner.startswith("["):
            envs = re.findall(r"['\"]([^'\"]+)['\"]", inner)
            if len(envs) == 1:
                return f"{{% if _app_env == '{envs[0]}' %}}"
            env_list = "[" + ", ".join(f"'{e}'" for e in envs) + "]"
            return f"{{% if _app_env in {env_list} %}}"
        # Single string form
        env_match = re.search(r"['\"]([^'\"]+)['\"]", inner)
        if env_match:
            return f"{{% if _app_env == '{env_match.group(1)}' %}}"
        return f"{{% if _app_env == {inner} %}}"

    source = _replace_paren_directive(source, "env", repl)
    source = source.replace("@endenv", "{% endif %}")
    return source


# ------------------------------------------------------------------
# @can / @endcan / @cannot / @endcannot
# ------------------------------------------------------------------


def _can(source: str) -> str:
    """@can('ability') → {% if can('ability') %}  (Gate.allows via injected can())"""

    def repl(inner: str) -> str:
        return f"{{% if can({inner}) %}}"

    source = _replace_paren_directive(source, "can", repl)
    source = source.replace("@endcan", "{% endif %}")
    return source


def _cannot(source: str) -> str:
    def repl(inner: str) -> str:
        return f"{{% if not can({inner}) %}}"

    source = _replace_paren_directive(source, "cannot", repl)
    source = source.replace("@endcannot", "{% endif %}")
    return source


# ------------------------------------------------------------------
# @once / @endonce — strip markers, content renders unconditionally
# (full "once per page" semantics require runtime state not available
#  at preprocess time; stripping the markers is a safe no-op default)
# ------------------------------------------------------------------


def _once(source: str) -> str:
    source = re.sub(r"@once\b", "", source)
    source = source.replace("@endonce", "")
    return source


# ------------------------------------------------------------------
# @py / @endpy — strip markers, body is treated as raw Jinja2
# ------------------------------------------------------------------


def _py(source: str) -> str:
    source = re.sub(r"@py\b", "", source)
    source = source.replace("@endpy", "")
    return source


# ------------------------------------------------------------------
# @stack / @push / @endpush / @prepend / @endprepend
# ------------------------------------------------------------------


def _stack(source: str) -> str:
    return re.sub(r"@stack\(\s*['\"](\w+)['\"]\s*\)", r"{{ _stacks.get('\1', '') | safe }}", source)


def _push(source: str) -> str:
    source = re.sub(r"@push\(\s*['\"](\w+)['\"]\s*\)", r"{% set _push_\1 %}", source)
    source = source.replace("@endpush", "{% endset %}")
    return source


def _prepend(source: str) -> str:
    """@prepend — treated as @push (prepend-order not enforced at preprocess time)."""
    source = re.sub(r"@prepend\(\s*['\"](\w+)['\"]\s*\)", r"{% set _push_\1 %}", source)
    source = source.replace("@endprepend", "{% endset %}")
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
