"""VIEW-03 (autoescape / configured extension) and VIEW-L1 (compiled-template cache)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import hunt
from hunt.view import factory as factory_module
from hunt.view.directives import preprocess
from hunt.view.factory import (
    ViewFactory,
    compiled_view_cache_name,
    prune_compiled_views,
    write_compiled_view,
)


def write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# VIEW-03
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extension", [".html", ".jinja", "jinja2", ".blade.html", ".txt"])
def test_autoescape_is_on_for_every_extension(tmp_path, extension):
    dotted = extension if extension.startswith(".") else f".{extension}"
    write(tmp_path, {f"page{dotted}": "<p>{{ msg }}</p>{!! raw !!}"})
    out = ViewFactory(tmp_path, extension=extension).make("page", {"msg": "<script>", "raw": "<b>"}).render()
    assert out == "<p>&lt;script&gt;</p><b>"


def test_directives_honour_the_configured_extension(tmp_path):
    write(
        tmp_path,
        {
            "layouts/app.jinja": "<main>@yield('content')</main>",
            "partials/nav-bar.jinja": "<nav>{{ who }}</nav>",
            "components/box.jinja": "<box>{{ label }}{{ _slot_default|default('') }}</box>",
            "page.jinja": (
                "@extends('layouts.app')"
                "@section('content')"
                "@include('partials.nav-bar', {'who': name})"
                "@includeIf('partials.missing')"
                "@includeFirst(['nope', 'partials.nav-bar'])"
                "@component('box', {'label': name})<i>slot</i>@endcomponent"
                "@endsection"
            ),
        },
    )
    out = ViewFactory(tmp_path, extension=".jinja").make("page", {"name": "<n>", "who": "ctx"}).render()
    assert out == "<main><nav>&lt;n&gt;</nav><nav>ctx</nav><box>&lt;n&gt;<i>slot</i></box></main>"


def test_preprocess_extension_argument():
    out = preprocess("@extends('a.b')@include('c')@component('d')", ".jinja")
    assert "{% extends 'a/b.jinja' %}" in out
    assert "{% include 'c.jinja' %}" in out
    assert "{% include 'components/d.jinja' %}" in out
    assert "{% extends 'a/b.html' %}" in preprocess("@extends('a.b')")


def test_bundled_html_templates_remain_reachable_with_a_custom_extension(tmp_path):
    write(tmp_path, {"page.jinja": "@component('alert', {'message': m})"})
    factory = ViewFactory(tmp_path, extension=".jinja")
    out = factory.make("page", {"m": "<x>"}).render()
    assert 'role="alert"' in out
    assert "&lt;x&gt;" in out
    # bundled auth views extend 'auth.layout' through the same fallback
    assert factory.exists("auth.login")
    html = factory.make("auth.login", {"status": "", "errors": {}, "config": lambda k, d=None: d}).render()
    assert "<h1>Sign in</h1>" in html
    assert not factory.exists("does.not.exist")
    with pytest.raises(FileNotFoundError):
        factory.make("does.not.exist")


def test_app_template_wins_over_bundled_fallback(tmp_path):
    write(tmp_path, {"components/alert.jinja": "MINE", "page.jinja": "@component('alert')"})
    assert ViewFactory(tmp_path, extension=".jinja").make("page").render() == "MINE"


# ---------------------------------------------------------------------------
# VIEW-L1
# ---------------------------------------------------------------------------

NAME_RE = re.compile(r"[0-9a-f]{32}-[0-9a-f]{16}")


def cached(cache: Path) -> list[str]:
    return sorted(p.name for p in cache.iterdir())


def test_cache_key_includes_the_framework_version(tmp_path, monkeypatch):
    views, cache = tmp_path / "views", tmp_path / "cache"
    write(views, {"page.html": "@if($x)yes@endif"})
    assert ViewFactory(views, cache).make("page", {"x": 1}).render() == "yes"
    (first,) = cached(cache)
    assert NAME_RE.fullmatch(first)
    assert first == compiled_view_cache_name(views / "page.html")

    # a stale compiled file from this version is trusted (proves the cache is read) ...
    (cache / first).write_text("STALE", encoding="utf-8")
    assert ViewFactory(views, cache).make("page", {"x": 1}).render() == "STALE"

    # ... but another framework version never reads it, and replaces it
    monkeypatch.setattr(hunt, "__version__", hunt.__version__ + ".post1")
    assert ViewFactory(views, cache).make("page", {"x": 1}).render() == "yes"
    (second,) = cached(cache)
    assert second != first
    assert second.split("-")[0] == first.split("-")[0]


def test_cache_key_changes_with_mtime_and_extension(tmp_path):
    views, cache = tmp_path / "views", tmp_path / "cache"
    write(views, {"page.html": "one"})
    page = views / "page.html"
    assert ViewFactory(views, cache).make("page").render() == "one"
    before = compiled_view_cache_name(page)
    assert compiled_view_cache_name(page, ".jinja") != before

    page.write_text("two", encoding="utf-8")
    stat = page.stat()
    os.utime(page, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert ViewFactory(views, cache).make("page").render() == "two"
    assert cached(cache) == [compiled_view_cache_name(page)]  # the old compiled copy was pruned
    assert compiled_view_cache_name(page) != before


def test_write_is_atomic(tmp_path, monkeypatch):
    views, cache = tmp_path / "views", tmp_path / "cache"
    write(views, {"page.html": "content"})
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        # the final file must not exist (or be partially written) before the rename
        assert not Path(dst).exists()
        assert Path(src).read_text(encoding="utf-8") == "content"
        assert Path(src).parent == Path(dst).parent
        replaced.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(factory_module.os, "replace", spy)
    assert ViewFactory(views, cache).make("page").render() == "content"
    assert len(replaced) == 1
    assert cached(cache) == [Path(replaced[0][1]).name]


def test_failed_write_leaves_no_partial_file_and_rendering_still_works(tmp_path, monkeypatch):
    views, cache = tmp_path / "views", tmp_path / "cache"
    write(views, {"page.html": "content"})

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(factory_module.os, "replace", boom)
    assert ViewFactory(views, cache).make("page").render() == "content"
    assert cached(cache) == []


def test_prune_compiled_views(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    current = "a" * 32 + "-" + "b" * 16
    legacy = "c" * 64
    old = "d" * 32 + "-" + "e" * 16
    for name in (current, legacy, old, ".hunt-view-tmp123", ".gitignore", "notes.txt"):
        (cache / name).write_text("x", encoding="utf-8")
    past = 1_000_000_000
    os.utime(cache / old, (past, past))
    os.utime(cache / legacy, (past, past))

    assert prune_compiled_views(cache, max_age=3600) == 2
    assert cached(cache) == sorted([current, ".hunt-view-tmp123", ".gitignore", "notes.txt"])
    assert prune_compiled_views(cache) == 2
    assert cached(cache) == [".gitignore", "notes.txt"]
    assert prune_compiled_views(tmp_path / "missing") == 0


def test_write_compiled_view_replaces_older_copies_of_the_same_template(tmp_path):
    ident, other = "a" * 32, "f" * 32
    write_compiled_view(tmp_path, f"{ident}-{'1' * 16}", "v1")
    write_compiled_view(tmp_path, f"{other}-{'1' * 16}", "other")
    write_compiled_view(tmp_path, f"{ident}-{'2' * 16}", "v2")
    assert cached(tmp_path) == [f"{ident}-{'2' * 16}", f"{other}-{'1' * 16}"]
    assert (tmp_path / f"{ident}-{'2' * 16}").read_text(encoding="utf-8") == "v2"


def test_factory_prune_cache(tmp_path):
    views, cache = tmp_path / "views", tmp_path / "cache"
    write(views, {"a.html": "A", "b.html": "B"})
    factory = ViewFactory(views, cache)
    factory.make("a").render()
    factory.make("b").render()
    assert len(cached(cache)) == 2
    assert factory.prune_cache(max_age=3600) == 0
    assert factory.prune_cache() == 2
    assert cached(cache) == []
    assert ViewFactory(views).prune_cache() == 0
