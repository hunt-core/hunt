"""
hunt ``@``-directive preprocessor.

The template source is scanned once into a token stream (plain text and
directive tokens) and the directives are then expanded into Jinja2 syntax.

Matching rules:

* A directive is only recognised as a whole token: ``@name`` where ``name`` is
  a complete identifier found in the directive table.  Unknown ``@word``
  tokens (Alpine.js ``@click``, CSS ``@media`` / ``@import`` / ``@keyframes``,
  ...) are passed through untouched.
* ``@`` preceded by a word character (``admin@authority.com``) never starts a
  directive.  The one exception keeps the compact form ``yes@endif`` working:
  a closing / continuation directive glued to the preceding word is accepted
  when its opening directive is currently open and it is not followed by
  something that looks like a host name (``.com``).
* ``@@name`` is an escape and renders a literal ``@name``.
* Nothing is matched inside Jinja ``{{ }}``, ``{% %}``, ``{# #}`` or
  ``{% raw %}`` regions, nor inside ``@verbatim`` blocks.
* Directives that take arguments need a balanced ``( ... )``; an unbalanced
  one raises :class:`jinja2.TemplateSyntaxError` with the line number.
"""

from __future__ import annotations

import re

from jinja2 import TemplateSyntaxError

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INTEREST_RE = re.compile(r"[{@]")
_SIMPLE_EXPR_RE = re.compile(r"[\w.]+")
_NAME_RE = re.compile(r"\w+")
_ADDRESS_TAIL_RE = re.compile(r"[.\-]\w")
_RAW_TAG_RE = re.compile(r"[-+]?\s*raw\s*[-+]?")
_ENDRAW_RE = re.compile(r"\{%[-+]?\s*endraw\s*[-+]?%\}")
_HSPACE_RE = re.compile(r"[ \t]*")

# Global functions the generated code calls for @stack / @push.  They are
# registered on the Jinja environment by ``hunt.view.factory``.
STACK_FUNCTION = "_hunt_stack"
PUSH_FUNCTION = "_hunt_push"


def _line_of(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _scan_parens(text: str, start: int) -> int | None:
    """Given the index of '(', return the index just after the matching ')' (None if unbalanced)."""
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
    return None


def _find_matching_paren(text: str, start: int) -> int:
    """Given the index of '(', return the index just after the matching ')'."""
    end = _scan_parens(text, start)
    return len(text) if end is None else end


def _find_close(text: str, start: int, closer: str) -> int | None:
    """Index of *closer* at or after *start*, ignoring quoted strings (None if absent)."""
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
        elif text.startswith(closer, i):
            return i
        i += 1
    return None


def _strip_dollars(expr: str) -> str:
    """Drop the PHP-style ``$`` sigil in front of identifiers (outside string literals)."""
    if "$" not in expr:
        return expr
    out: list[str] = []
    in_string: str | None = None
    i = 0
    while i < len(expr):
        ch = expr[i]
        if in_string:
            if ch == "\\" and i + 1 < len(expr):
                out.append(expr[i : i + 2])
                i += 2
                continue
            if ch == in_string:
                in_string = None
            out.append(ch)
        elif ch in ('"', "'"):
            in_string = ch
            out.append(ch)
        elif ch == "$" and i + 1 < len(expr) and (expr[i + 1].isalpha() or expr[i + 1] == "_"):
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _literal(expr: str) -> str | None:
    """Return the contents of a simple quoted string literal, or None."""
    expr = expr.strip()
    if len(expr) >= 2 and expr[0] in ("'", '"') and expr[-1] == expr[0]:
        body = expr[1:-1]
        if expr[0] not in body and "\\" not in body:
            return body
    return None


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _group(expr: str) -> str:
    """Parenthesise *expr* unless it is a plain dotted name / number."""
    return expr if _SIMPLE_EXPR_RE.fullmatch(expr) else f"({expr})"


def _view_path(name: str, extension: str) -> str:
    """``partials.nav-bar`` → ``partials/nav-bar<extension>``."""
    if name.endswith(extension) or name.endswith(".html"):
        return name
    return name.replace(".", "/") + extension


# ------------------------------------------------------------------
# Directive table
# ------------------------------------------------------------------

# Directives that take a parenthesised argument list.
_WITH_ARGS = frozenset(
    {
        "json",
        "extends",
        "section",
        "yield",
        "includeFirst",
        "includeWhen",
        "includeIf",
        "include",
        "forelse",
        "foreach",
        "for",
        "while",
        "switch",
        "case",
        "if",
        "elseif",
        "unless",
        "error",
        "old",
        "env",
        "can",
        "cannot",
        "stack",
        "push",
        "prepend",
        "component",
        "slot",
        "method",
    }
)

# closing directive -> directives it can close
_CLOSERS: dict[str, tuple[str, ...]] = {
    "endsection": ("section",),
    "stop": ("section",),
    "show": ("section",),
    "endforelse": ("forelse",),
    "endforeach": ("foreach",),
    "endfor": ("for",),
    "endwhile": ("while",),
    "endswitch": ("switch",),
    "endif": ("if",),
    "endunless": ("unless",),
    "enderror": ("error",),
    "endauth": ("auth",),
    "endguest": ("guest",),
    "endenv": ("env",),
    "endcan": ("can",),
    "endcannot": ("cannot",),
    "endonce": ("once",),
    "endpy": ("py",),
    "endpush": ("push",),
    "endprepend": ("prepend",),
    "endcomponent": ("component",),
    "endslot": ("slot",),
}

_IF_LIKE = ("if", "unless", "error", "auth", "guest", "env", "can", "cannot")

# continuation directive -> blocks it can appear in
_CONTINUATIONS: dict[str, tuple[str, ...]] = {
    "else": (*_IF_LIKE, "for", "foreach", "forelse"),
    "elseif": _IF_LIKE,
    "empty": ("forelse", "foreach"),
    "case": ("switch",),
    "break": ("switch",),
    "default": ("switch",),
}

# Directives that are only meaningful inside another one; elsewhere the text is left alone.
_CONTEXT_ONLY: dict[str, str] = {
    "case": "switch",
    "break": "switch",
    "default": "switch",
    "slot": "component",
    "endslot": "component",
}

_OPENERS = frozenset(name for names in _CLOSERS.values() for name in names)

_NO_ARGS = frozenset(
    {
        *_CLOSERS,
        "else",
        "empty",
        "break",
        "default",
        "csrf",
        "errors",
        "auth",
        "guest",
        "once",
        "py",
        "verbatim",
        "endverbatim",
    }
)

_DIRECTIVES = _WITH_ARGS | _NO_ARGS


class _Token:
    __slots__ = ("args", "name", "pos", "text")

    def __init__(self, name: str | None, text: str = "", args: str = "", pos: int = 0) -> None:
        self.name = name  # None for plain text
        self.text = text
        self.args = args
        self.pos = pos


class _State:
    def __init__(self, source: str, extension: str) -> None:
        self.source = source
        self.extension = extension
        self.pairs: dict[int, int] = {}
        self.switches: list[list] = []  # [subject, seen_case]
        self.slot_scopes: list[set[str]] = []

    def error(self, message: str, token: _Token) -> TemplateSyntaxError:
        return TemplateSyntaxError(message, _line_of(self.source, token.pos))


def preprocess(source: str, extension: str = ".html") -> str:
    """Transform hunt @-directives into Jinja2 syntax.

    *extension* is the configured view extension; it is appended to the names
    given to ``@extends`` / ``@include`` / ``@component``.
    """
    if not extension.startswith("."):
        extension = f".{extension}"
    tokens = _tokenize(source)
    state = _State(source, extension)
    state.pairs = _pair_blocks(tokens, state)
    return _emit(tokens, 0, len(tokens), state, None)


# ------------------------------------------------------------------
# Tokenizer
# ------------------------------------------------------------------


def _scan_brace(source: str, j: int, buf: list[str]) -> int:
    """Handle a ``{`` at *j*: Jinja / Blade delimiters. Returns the next index."""
    if source.startswith("{{--", j):
        # {{-- comment --}}
        end = source.find("--}}", j + 4)
        if end != -1:
            return end + 4
    elif source.startswith("{{", j):
        # {{ $user.name }} → {{ user.name }}
        end = _find_close(source, j + 2, "}}")
        if end is not None:
            buf.append("{{" + _strip_dollars(source[j + 2 : end]) + "}}")
            return end + 2
    elif source.startswith("{!!", j):
        # Raw echo: {!! $var !!} → {{ var | safe }}
        # WARNING: outputs unescaped HTML — never use with user-supplied data (XSS risk).
        end = source.find("!!}", j + 3)
        if end != -1:
            expr = _strip_dollars(source[j + 3 : end]).strip()
            buf.append("{{ " + _group(expr) + " | safe }}")
            return end + 3
    elif source.startswith("{%", j):
        end = _find_close(source, j + 2, "%}")
        if end is not None:
            if _RAW_TAG_RE.fullmatch(source[j + 2 : end]):
                m = _ENDRAW_RE.search(source, end + 2)
                if m:
                    buf.append(source[j : m.end()])
                    return m.end()
            buf.append(source[j : end + 2])
            return end + 2
    elif source.startswith("{#", j):
        end = source.find("#}", j + 2)
        if end != -1:
            buf.append(source[j : end + 2])
            return end + 2
    buf.append("{")
    return j + 1


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    buf: list[str] = []
    open_counts: dict[str, int] = {}
    n = len(source)
    i = 0
    directive_end = -1  # end offset of the last directive token

    def flush() -> None:
        if buf:
            tokens.append(_Token(None, "".join(buf)))
            buf.clear()

    while i < n:
        found = _INTEREST_RE.search(source, i)
        if not found:
            buf.append(source[i:])
            break
        j = found.start()
        buf.append(source[i:j])
        if source[j] == "{":
            i = _scan_brace(source, j, buf)
            continue

        prev = source[j - 1] if j > 0 else ""
        # "glued": the @ follows a word character (an e-mail address, most of the time)
        glued = j != directive_end and (prev == "@" or prev == "_" or prev.isalnum())

        # @@name → literal @name
        if source.startswith("@@", j):
            m = _IDENT_RE.match(source, j + 2)
            if m and not glued:
                buf.append("@" + m.group())
                i = m.end()
            else:
                buf.append("@@")
                i = j + 2
            continue

        m = _IDENT_RE.match(source, j + 1)
        if not m:
            buf.append("@")
            i = j + 1
            continue
        name = m.group()
        after = m.end()
        literal_end = after  # where to resume when the token turns out not to be a directive

        accepted = name in _DIRECTIVES
        if accepted and name in _CONTEXT_ONLY and not open_counts.get(_CONTEXT_ONLY[name]):
            accepted = False
        if accepted and glued:
            owners = _CLOSERS.get(name) or _CONTINUATIONS.get(name) or ()
            accepted = any(open_counts.get(o) for o in owners) and not _ADDRESS_TAIL_RE.match(source, after)

        args = ""
        if accepted and name in _WITH_ARGS:
            paren = _HSPACE_RE.match(source, after).end()
            if paren < n and source[paren] == "(":
                end = _scan_parens(source, paren)
                if end is None:
                    raise TemplateSyntaxError(f"Unbalanced parentheses in @{name} directive.", _line_of(source, j))
                args = source[paren + 1 : end - 1]
                after = end
            else:
                accepted = False

        if not accepted:
            buf.append(source[j:literal_end])
            i = literal_end
            continue

        if name == "verbatim":
            end = source.find("@endverbatim", after)
            if end == -1:
                raise TemplateSyntaxError("@verbatim without a matching @endverbatim.", _line_of(source, j))
            buf.append("{% raw %}" + source[after:end] + "{% endraw %}")
            i = directive_end = end + len("@endverbatim")
            continue
        if name == "endverbatim":
            raise TemplateSyntaxError("@endverbatim without a matching @verbatim.", _line_of(source, j))

        if name in _OPENERS:
            # the inline forms @section('a', 'b') / @slot('a', 'b') do not open a block
            if name not in ("section", "slot") or len(_split_by_sep(args, ",")) == 1:
                open_counts[name] = open_counts.get(name, 0) + 1
        elif name in _CLOSERS:
            for owner in _CLOSERS[name]:
                if open_counts.get(owner):
                    open_counts[owner] -= 1
                    break

        flush()
        tokens.append(_Token(name, source[j:after], args, j))
        i = directive_end = after

    flush()
    return tokens


# ------------------------------------------------------------------
# Block pairing: @component / @slot / @push / @prepend
# ------------------------------------------------------------------


def _is_blank(tokens: list[_Token], lo: int, hi: int) -> bool:
    return all(t.name is None and not t.text.strip() for t in tokens[lo:hi])


def _pair_blocks(tokens: list[_Token], state: _State) -> dict[int, int]:
    """
    Map the index of every block-opening token to the index of its closing token.

    ``@component`` may be self-closing (no ``@endcomponent``) so the pairing is
    done with a depth stack: every ``@endcomponent`` closes the most recent
    ``@component`` that is still open, and components left open at the end are
    self-closing.  That makes a self-closing component followed by a block
    component, and nested block components, both work.

    One layout stays ambiguous::

        @component('card') @component('badge', {...}) @endcomponent

    A block component with an empty body is pointless, so when the component
    an ``@endcomponent`` would close has a blank body and an earlier component
    at the same level is still open, the ``@endcomponent`` closes that earlier
    one instead (card containing a self-closing badge).
    """
    pairs: dict[int, int] = {}
    components: list[int] = []
    component_pairs: list[tuple[int, int]] = []
    simple: dict[str, list[int]] = {"slot": [], "push": [], "prepend": []}

    for idx, tok in enumerate(tokens):
        name = tok.name
        if name is None:
            continue
        if name == "component":
            components.append(idx)
        elif name == "endcomponent":
            if not components:
                raise state.error("@endcomponent without a matching @component.", tok)
            open_idx = components.pop()
            pairs[open_idx] = idx
            component_pairs.append((open_idx, idx))
        elif name in simple:
            if name == "slot" and len(_split_by_sep(tok.args, ",")) > 1:
                continue  # inline @slot('name', value)
            simple[name].append(idx)
        elif name in ("endslot", "endpush", "endprepend"):
            opener = name[3:]
            if not simple[opener]:
                raise state.error(f"@{name} without a matching @{opener}.", tok)
            pairs[simple[opener].pop()] = idx

    for opener, stack in simple.items():
        if stack:
            raise state.error(f"@{opener} without a matching @end{opener}.", tokens[stack[-1]])

    # Empty-body heuristic (see docstring). *components* now holds the self-closing ones.
    for open_idx, end_idx in component_pairs:
        if not _is_blank(tokens, open_idx + 1, end_idx):
            continue
        parent_open = max((o for o, e in pairs.items() if o < open_idx and e > end_idx), default=-1)
        earlier = [c for c in components if parent_open < c < open_idx]
        if earlier:
            outer = max(earlier)
            components.remove(outer)
            components.append(open_idx)
            del pairs[open_idx]
            pairs[outer] = end_idx

    return pairs


# ------------------------------------------------------------------
# Component helpers
# ------------------------------------------------------------------


def _split_by_sep(s: str, sep: str, maxsplit: int = -1) -> list[str]:
    """Split *s* on *sep* at bracket/string depth 0."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string: str | None = None
    splits = 0
    i = 0
    while i < len(s):
        ch = s[i]
        if in_string:
            if ch == "\\" and i + 1 < len(s):
                current.append(s[i : i + 2])
                i += 2
                continue
            if ch == in_string:
                in_string = None
            current.append(ch)
        elif ch in ('"', "'"):
            in_string = ch
            current.append(ch)
        elif ch in ("(", "[", "{"):
            depth += 1
            current.append(ch)
        elif ch in (")", "]", "}"):
            depth -= 1
            current.append(ch)
        elif depth == 0 and s[i : i + len(sep)] == sep:
            if maxsplit < 0 or splits < maxsplit:
                parts.append("".join(current))
                current = []
                splits += 1
                i += len(sep)
                continue
            else:
                current.append(ch)
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _normalize_php_array(s: str) -> str:
    """Convert PHP-style ['key' => val] to {'key': val}."""
    s = s.strip()
    if not s.startswith("["):
        return s
    inner = s[1:-1]
    # Replace => with : outside of quoted strings
    result: list[str] = []
    in_string: str | None = None
    j = 0
    while j < len(inner):
        ch = inner[j]
        if in_string:
            if ch == "\\" and j + 1 < len(inner):
                result.append(inner[j : j + 2])
                j += 2
                continue
            if ch == in_string:
                in_string = None
            result.append(ch)
        elif ch in ('"', "'"):
            in_string = ch
            result.append(ch)
        elif inner[j : j + 2] == "=>":
            result.append(":")
            j += 2
            continue
        else:
            result.append(ch)
        j += 1
    return "{" + "".join(result) + "}"


def _props_to_with_args(props_str: str) -> str:
    """
    Convert a dict/array literal to Jinja2 ``{% with %}`` keyword arguments.

    ``{'title': 'Hello', 'rows': items}`` → ``title='Hello', rows=items``
    """
    s = _normalize_php_array(props_str.strip())
    if not s:
        return ""
    # Strip outer braces
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    if not s:
        return ""
    pairs = _split_by_sep(s, ",")
    assignments: list[str] = []
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        parts = _split_by_sep(pair, ":", maxsplit=1)
        if len(parts) != 2:
            continue
        key = parts[0].strip().strip("'\"")
        val = parts[1].strip()
        if not key:
            continue
        assignments.append(f"{key}={val}")
    return ", ".join(assignments)


def _parse_component_open(inner: str) -> tuple[str, str]:
    """
    Parse the inside of ``@component(...)`` → (name, with_args).

    *inner* is the raw string between the outer parentheses, e.g.
    ``'card', {'title': 'Hello'}`` or just ``'card'``.
    """
    name_match = re.match(r"\s*['\"]([^'\"]+)['\"]\s*", inner)
    if not name_match:
        return "unknown", ""
    name = name_match.group(1)
    rest = inner[name_match.end() :].lstrip().lstrip(",").lstrip()
    with_args = _props_to_with_args(rest) if rest else ""
    return name, with_args


def _slot_name(expr: str, tok: _Token, state: _State) -> str:
    name = _literal(expr)
    if name is None or not _NAME_RE.fullmatch(name):
        raise state.error("@slot expects a quoted slot name made of letters, digits and underscores.", tok)
    return name


def _direct_slot_names(tokens: list[_Token], lo: int, hi: int, state: _State) -> set[str]:
    """Names of the slots declared directly inside tokens[lo:hi] (not in nested components)."""
    names: set[str] = set()
    i = lo
    while i < hi:
        tok = tokens[i]
        if tok.name == "component" and i in state.pairs:
            i = state.pairs[i] + 1
            continue
        if tok.name == "slot":
            names.add(_slot_name(_split_by_sep(tok.args, ",")[0], tok, state))
        i += 1
    return names


def _emit_component(tokens: list[_Token], idx: int, end: int | None, state: _State) -> str:
    """
    Expand one ``@component``.

    Self-closing::

        @component('alert', {'type': 'success', 'message': msg})
        →
        {% with %}{% set _slot_default = '' %}
        {% with type='success', message=msg %}{% include 'components/alert.html' %}{% endwith %}
        {% endwith %}

    Block form with slots::

        @component('modal', {'title': 'Confirm'})
            @slot('footer')<button>OK</button>@endslot
            Body text
        @endcomponent
        →
        {% with %}
        {% set _slot_footer %}<button>OK</button>{% endset %}
        {% set _slot_default %}Body text{% endset %}
        {% with title='Confirm' %}{% include 'components/modal.html' %}{% endwith %}
        {% endwith %}

    The outer ``{% with %}`` scopes the slot variables to this component, and
    slots that this component does not fill are reset so that they do not leak
    in from an enclosing component.
    """
    tok = tokens[idx]
    inner = _strip_dollars(tok.args)
    if not re.match(r"\s*['\"][^'\"]+['\"]\s*(,|$)", inner):
        raise state.error("@component expects a quoted component name as its first argument.", tok)
    name, with_args = _parse_component_open(inner)
    tmpl = _quote("components/" + _view_path(name, state.extension))

    slots: dict[str, str] = {}
    default = ""
    if end is not None:
        state.slot_scopes.append(_direct_slot_names(tokens, idx + 1, end, state))
        try:
            default = _emit(tokens, idx + 1, end, state, slots).strip()
        finally:
            state.slot_scopes.pop()

    parts: list[str] = ["{% with %}"]
    inherited = {"default"}.union(*state.slot_scopes)
    for slot in sorted(inherited - set(slots) - ({"default"} if default else set())):
        parts.append(f"{{% set _slot_{slot} = '' %}}")
    parts.extend(slots.values())
    if default:
        parts.append(f"{{% set _slot_default %}}{default}{{% endset %}}")
    if with_args:
        parts.append(f"{{% with {with_args} %}}{{% include {tmpl} %}}{{% endwith %}}")
    else:
        parts.append(f"{{% include {tmpl} %}}")
    parts.append("{% endwith %}")
    return "".join(parts)


# ------------------------------------------------------------------
# Emission
# ------------------------------------------------------------------


def _emit(tokens: list[_Token], lo: int, hi: int, state: _State, slots: dict[str, str] | None) -> str:
    """Render tokens[lo:hi] to Jinja source. *slots* collects ``@slot`` blocks of the enclosing component."""
    out: list[str] = []
    i = lo
    while i < hi:
        tok = tokens[i]
        name = tok.name
        if state.pairs.get(i, -1) >= hi:
            raise state.error(f"@{name} is closed outside the block it was opened in.", tok)
        if name is None:
            out.append(tok.text)
            i += 1
        elif name == "component":
            end = state.pairs.get(i)
            out.append(_emit_component(tokens, i, end, state))
            i = i + 1 if end is None else end + 1
        elif name == "slot":
            if slots is None:
                raise state.error("@slot must be placed directly inside a @component ... @endcomponent block.", tok)
            slot_args = _split_by_sep(_strip_dollars(tok.args), ",", maxsplit=1)
            slot = _slot_name(slot_args[0], tok, state)
            if len(slot_args) > 1:
                slots[slot] = f"{{% set _slot_{slot} = {slot_args[1].strip()} %}}"
                i += 1
            else:
                end = state.pairs[i]
                slots[slot] = f"{{% set _slot_{slot} %}}{_emit(tokens, i + 1, end, state, None)}{{% endset %}}"
                i = end + 1
        elif name in ("push", "prepend"):
            end = state.pairs[i]
            stack = _literal(tok.args)
            if stack is None or not _NAME_RE.fullmatch(stack):
                raise state.error(f"@{name} expects a quoted stack name.", tok)
            body = _emit(tokens, i + 1, end, state, None)
            flag = ", true" if name == "prepend" else ""
            out.append(
                f"{{% set _hunt_pushed %}}{body}{{% endset %}}"
                f"{{% set _hunt_pushed = {PUSH_FUNCTION}('{stack}', _hunt_pushed{flag}) %}}"
            )
            i = end + 1
        elif name in ("endslot", "endpush", "endprepend"):
            raise state.error(f"Unexpected @{name}.", tok)
        elif name == "endcomponent":
            # only reachable when the pairing heuristic left it without an owner in this range
            raise state.error("@endcomponent without a matching @component.", tok)
        else:
            out.append(_HANDLERS[name](tok, state))
            i += 1
    return "".join(out)


def _args(tok: _Token) -> str:
    return _strip_dollars(tok.args).strip()


def _template_ref(expr: str, state: _State) -> str:
    """A quoted view name becomes a template path; anything else is passed on as a Jinja expression."""
    name = _literal(expr)
    if name is None:
        return expr.strip()
    return _quote(_view_path(name, state.extension))


def _with_data(body: str, data: str | None, tok: _Token, state: _State) -> str:
    """Wrap an include in ``{% with %}`` when a data argument was given."""
    if data is None or not data.strip():
        return body
    data = data.strip()
    with_args = _props_to_with_args(data)
    if not with_args:
        if data in ("{}", "[]"):
            return body
        raise state.error(
            f"The data argument of @{tok.name} must be a literal dict, e.g. {{'key': value}}.",
            tok,
        )
    return f"{{% with {with_args} %}}{body}{{% endwith %}}"


# @json($var) / @json($var, flags) → {{ var | tojson }}
def _h_json(tok: _Token, state: _State) -> str:
    var = _split_by_sep(_args(tok), ",")[0].strip()
    return f"{{{{ {_group(var)} | tojson }}}}"


def _h_extends(tok: _Token, state: _State) -> str:
    return f"{{% extends {_template_ref(_args(tok), state)} %}}"


def _block_name(expr: str, tok: _Token, state: _State) -> str:
    name = _literal(expr)
    if name is None:
        name = expr.strip()
    name = re.sub(r"[^\w]", "_", name)
    if not name or name[0].isdigit():
        raise state.error(f"Invalid section name in @{tok.name}.", tok)
    return name


def _inline_content(expr: str) -> str:
    text = _literal(expr)
    if text is not None:
        return text
    return f"{{{{ {expr.strip()} }}}}"


# @section('name') ... @endsection   /   @section('name', 'value')
def _h_section(tok: _Token, state: _State) -> str:
    parts = _split_by_sep(_args(tok), ",", maxsplit=1)
    name = _block_name(parts[0], tok, state)
    if len(parts) == 1:
        return f"{{% block {name} %}}"
    return f"{{% block {name} %}}{_inline_content(parts[1])}{{% endblock %}}"


def _h_endblock(tok: _Token, state: _State) -> str:
    return "{% endblock %}"


# @yield('name') / @yield('name', 'default')
def _h_yield(tok: _Token, state: _State) -> str:
    parts = _split_by_sep(_args(tok), ",", maxsplit=1)
    name = _block_name(parts[0], tok, state)
    default = _inline_content(parts[1]) if len(parts) > 1 else ""
    return f"{{% block {name} %}}{default}{{% endblock %}}"


# @include('view.name') / @include('view.name', {'key': value})
def _h_include(tok: _Token, state: _State) -> str:
    parts = _split_by_sep(_args(tok), ",", maxsplit=1)
    body = f"{{% include {_template_ref(parts[0], state)} %}}"
    return _with_data(body, parts[1] if len(parts) > 1 else None, tok, state)


# @includeIf('view.name') → {% include '...' ignore missing %}
def _h_include_if(tok: _Token, state: _State) -> str:
    parts = _split_by_sep(_args(tok), ",", maxsplit=1)
    body = f"{{% include {_template_ref(parts[0], state)} ignore missing %}}"
    return _with_data(body, parts[1] if len(parts) > 1 else None, tok, state)


# @includeWhen($cond, 'view', [...]) → {% if cond %}{% include '...' %}{% endif %}
def _h_include_when(tok: _Token, state: _State) -> str:
    parts = _split_by_sep(_args(tok), ",", maxsplit=2)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise state.error("@includeWhen expects a condition and a view name.", tok)
    body = f"{{% include {_template_ref(parts[1], state)} %}}"
    body = _with_data(body, parts[2] if len(parts) > 2 else None, tok, state)
    return f"{{% if {parts[0].strip()} %}}{body}{{% endif %}}"


# @includeFirst(['view.a', 'view.b']) → {% include [...] ignore missing %}
def _h_include_first(tok: _Token, state: _State) -> str:
    inner = _args(tok)
    data: str | None = None
    if inner.startswith("["):
        parts = _split_by_sep(inner, ",", maxsplit=1)
        names_src = parts[0]
        data = parts[1] if len(parts) > 1 else None
    else:
        names_src = inner
    names = re.findall(r"['\"]([^'\"]+)['\"]", names_src)
    if not names:
        raise state.error("@includeFirst expects a list of quoted view names.", tok)
    path_list = "[" + ", ".join(_quote(_view_path(n, state.extension)) for n in names) + "]"
    return _with_data(f"{{% include {path_list} ignore missing %}}", data, tok, state)


_AS_RE = re.compile(r"(.+?)\s+as\s+(\w+)(?:\s*=>\s*(\w+))?", re.DOTALL)
_IN_RE = re.compile(r"[\w\s,()]+?\s+in\s+.+", re.DOTALL)


# @foreach($items as $item) / @foreach($map as $key => $value) / @forelse(...)
def _h_foreach(tok: _Token, state: _State) -> str:
    inner = _args(tok)
    m = _AS_RE.fullmatch(inner)
    if m:
        collection, first, second = m.group(1).strip(), m.group(2), m.group(3)
        if second:
            return f"{{% for {first}, {second} in {_group(collection)}.items() %}}"
        return f"{{% for {first} in {collection} %}}"
    if _IN_RE.fullmatch(inner):
        return f"{{% for {inner} %}}"
    raise state.error(f"@{tok.name} expects '$collection as $item' or '$collection as $key => $value'.", tok)


def _h_endfor(tok: _Token, state: _State) -> str:
    return "{% endfor %}"


def _h_else(tok: _Token, state: _State) -> str:
    return "{% else %}"


_FOR_RE = re.compile(
    r"(\w+)\s*=\s*(.+?)\s*;\s*\w+\s*(<=|>=|<|>)\s*(.+?)\s*;\s*\w+\s*"
    r"(?:(\+\+|--)(?:\s*\+\s*(\d+))?|(\+=|-=)\s*(.+?))",
    re.DOTALL,
)


# @for($i = 0; $i < 10; $i++) → {% for i in range(0, 10, 1) %}
def _h_for(tok: _Token, state: _State) -> str:
    inner = _args(tok)
    m = _FOR_RE.fullmatch(inner)
    if not m:
        if _IN_RE.fullmatch(inner):
            return f"{{% for {inner} %}}"
        raise state.error("@for expects '$i = start; $i < end; $i++' (or 'item in items').", tok)
    var, start, op, end = m.group(1), m.group(2), m.group(3), m.group(4)
    if m.group(5):
        ascending = m.group(5) == "++"
        step = m.group(6) or "1"
    else:
        ascending = m.group(7) == "+="
        step = _group(m.group(8).strip())
    if ascending != (op in ("<", "<=")):
        raise state.error("@for: the comparison and the increment go in opposite directions.", tok)
    start, end = _group(start), _group(end)
    if ascending:
        # the upper bound of range() is exclusive: only an inclusive comparison needs + 1
        stop = f"{end} + 1" if op == "<=" else end
        return f"{{% for {var} in range({start}, {stop}, {step}) %}}"
    stop = f"{end} - 1" if op == ">=" else end
    return f"{{% for {var} in range({start}, {stop}, -{step}) %}}"


def _h_while(tok: _Token, state: _State) -> str:
    raise state.error(
        f"@{tok.name} is not supported: Jinja2 has no while loop. Use @for or @foreach instead.",
        tok,
    )


# @switch / @case / @break / @default / @endswitch → if / elif / else
def _h_switch(tok: _Token, state: _State) -> str:
    state.switches.append([_group(_args(tok)), False])
    return ""


def _h_case(tok: _Token, state: _State) -> str:
    if not state.switches:
        return tok.text
    switch = state.switches[-1]
    keyword = "elif" if switch[1] else "if"
    switch[1] = True
    return f"{{% {keyword} {switch[0]} == {_args(tok)} %}}"


def _h_break(tok: _Token, state: _State) -> str:
    return "" if state.switches else tok.text


def _h_default(tok: _Token, state: _State) -> str:
    if not state.switches:
        return tok.text
    switch = state.switches[-1]
    if switch[1]:
        return "{% else %}"
    switch[1] = True
    return "{% if true %}"


def _h_endswitch(tok: _Token, state: _State) -> str:
    if not state.switches:
        raise state.error("@endswitch without a matching @switch.", tok)
    return "{% endif %}" if state.switches.pop()[1] else ""


def _h_if(tok: _Token, state: _State) -> str:
    return f"{{% if {_args(tok)} %}}"


def _h_elseif(tok: _Token, state: _State) -> str:
    return f"{{% elif {_args(tok)} %}}"


def _h_unless(tok: _Token, state: _State) -> str:
    return f"{{% if not {_group(_args(tok))} %}}"


def _h_endif(tok: _Token, state: _State) -> str:
    return "{% endif %}"


def _h_csrf(tok: _Token, state: _State) -> str:
    return '<input type="hidden" name="_token" value="{{ csrf_token }}">'


# @method('PUT') → hidden _method field (HTML forms can only send GET and POST)
def _h_method(tok: _Token, state: _State) -> str:
    inner = _args(tok)
    verb = _literal(inner)
    if verb is None:
        return f'<input type="hidden" name="_method" value="{{{{ {inner} }}}}">'
    verb = verb.strip().upper()
    if not re.fullmatch(r"[A-Z]+", verb):
        raise state.error("@method expects an HTTP verb such as 'PUT', 'PATCH' or 'DELETE'.", tok)
    return f'<input type="hidden" name="_method" value="{verb}">'


# @error('field') ... @enderror
def _h_error(tok: _Token, state: _State) -> str:
    field = _literal(_args(tok))
    if field is None:
        raise state.error("@error expects a quoted field name.", tok)
    return f"{{% if errors is defined and {_quote(field)} in errors %}}"


# @old('field') / @old('field', 'default') → {{ old('field') }}
def _h_old(tok: _Token, state: _State) -> str:
    return f"{{{{ old({_args(tok)}) }}}}"


# @errors — display all validation errors as a summary block
_ERRORS_HTML = (
    "{% if errors is defined and errors %}"
    '<div class="rounded-md bg-red-50 border border-red-200 p-4 mb-4">'
    '<ul class="list-disc list-inside text-sm text-red-800 space-y-0.5">'
    "{% for _ef, _ems in errors.items() %}{% for _em in _ems %}<li>{{ _em }}</li>{% endfor %}{% endfor %}"
    "</ul></div>"
    "{% endif %}"
)


def _h_errors(tok: _Token, state: _State) -> str:
    return _ERRORS_HTML


def _h_auth(tok: _Token, state: _State) -> str:
    return "{% if auth_user is defined and (auth_user() if auth_user is callable else auth_user) %}"


def _h_guest(tok: _Token, state: _State) -> str:
    return "{% if auth_user is not defined or not (auth_user() if auth_user is callable else auth_user) %}"


# @env('production') or @env(['production', 'staging']) → if _app_env check
def _h_env(tok: _Token, state: _State) -> str:
    inner = _args(tok)
    if inner.startswith("["):
        envs = re.findall(r"['\"]([^'\"]+)['\"]", inner)
        if len(envs) == 1:
            return f"{{% if _app_env == {_quote(envs[0])} %}}"
        env_list = "[" + ", ".join(_quote(e) for e in envs) + "]"
        return f"{{% if _app_env in {env_list} %}}"
    env = _literal(inner)
    if env is not None:
        return f"{{% if _app_env == {_quote(env)} %}}"
    return f"{{% if _app_env == {inner} %}}"


# @can('ability') → {% if can('ability') %}  (Gate.allows via injected can())
def _h_can(tok: _Token, state: _State) -> str:
    return f"{{% if can({_args(tok)}) %}}"


def _h_cannot(tok: _Token, state: _State) -> str:
    return f"{{% if not can({_args(tok)}) %}}"


# @once / @endonce and @py / @endpy: the markers are stripped, the body is plain Jinja2.
# (full "once per page" semantics are not implemented)
def _h_strip(tok: _Token, state: _State) -> str:
    return ""


# @stack('name') → placeholder that the view replaces with everything pushed during the render
def _h_stack(tok: _Token, state: _State) -> str:
    stack = _literal(_args(tok))
    if stack is None or not _NAME_RE.fullmatch(stack):
        raise state.error("@stack expects a quoted stack name.", tok)
    return f"{{{{ {STACK_FUNCTION}('{stack}') }}}}"


_HANDLERS = {
    "json": _h_json,
    "extends": _h_extends,
    "section": _h_section,
    "endsection": _h_endblock,
    "stop": _h_endblock,
    "show": _h_endblock,
    "yield": _h_yield,
    "include": _h_include,
    "includeIf": _h_include_if,
    "includeWhen": _h_include_when,
    "includeFirst": _h_include_first,
    "forelse": _h_foreach,
    "foreach": _h_foreach,
    "endforelse": _h_endfor,
    "endforeach": _h_endfor,
    "empty": _h_else,
    "for": _h_for,
    "endfor": _h_endfor,
    "while": _h_while,
    "endwhile": _h_while,
    "switch": _h_switch,
    "case": _h_case,
    "break": _h_break,
    "default": _h_default,
    "endswitch": _h_endswitch,
    "if": _h_if,
    "elseif": _h_elseif,
    "else": _h_else,
    "endif": _h_endif,
    "unless": _h_unless,
    "endunless": _h_endif,
    "csrf": _h_csrf,
    "method": _h_method,
    "error": _h_error,
    "enderror": _h_endif,
    "old": _h_old,
    "errors": _h_errors,
    "auth": _h_auth,
    "endauth": _h_endif,
    "guest": _h_guest,
    "endguest": _h_endif,
    "env": _h_env,
    "endenv": _h_endif,
    "can": _h_can,
    "endcan": _h_endif,
    "cannot": _h_cannot,
    "endcannot": _h_endif,
    "once": _h_strip,
    "endonce": _h_strip,
    "py": _h_strip,
    "endpy": _h_strip,
    "stack": _h_stack,
}
