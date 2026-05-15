"""Phase H tests: Blade / Templating Gaps."""
from __future__ import annotations

import pytest
from hunt.view.directives import preprocess


# ===========================================================================
# @verbatim / @endverbatim
# ===========================================================================

class TestVerbatim:
    def test_verbatim_wraps_in_raw(self):
        src = "@verbatim{{ $notProcessed }}@endverbatim"
        out = preprocess(src)
        assert "{% raw %}" in out
        assert "{% endraw %}" in out
        assert "{{ $notProcessed }}" in out

    def test_verbatim_protects_dollar_vars(self):
        src = "@verbatim{{ $user }}@endverbatim"
        out = preprocess(src)
        # Dollar var should NOT be stripped inside verbatim
        assert "{{ $user }}" in out
        assert "{{ user }}" not in out

    def test_verbatim_multiline(self):
        src = "@verbatim\n{% for x in y %}\n{{ x }}\n{% endfor %}\n@endverbatim"
        out = preprocess(src)
        assert "{% raw %}" in out
        assert "{% for x in y %}" in out

    def test_verbatim_does_not_affect_content_outside(self):
        src = "{{ $name }}@verbatim{{ $raw }}@endverbatim{{ $other }}"
        out = preprocess(src)
        assert "{{ name }}" in out       # outside → processed
        assert "{{ $raw }}" in out        # inside → preserved
        assert "{{ other }}" in out       # outside → processed


# ===========================================================================
# @json
# ===========================================================================

class TestJson:
    def test_json_simple_variable(self):
        src = "@json($data)"
        out = preprocess(src)
        assert "{{ data | tojson }}" in out

    def test_json_without_dollar(self):
        src = "@json(items)"
        out = preprocess(src)
        assert "{{ items | tojson }}" in out

    def test_json_with_flags_ignores_second_arg(self):
        src = "@json($config, JSON_PRETTY_PRINT)"
        out = preprocess(src)
        assert "{{ config | tojson }}" in out


# ===========================================================================
# @forelse / @empty / @endforelse
# ===========================================================================

class TestForelse:
    def test_forelse_basic(self):
        src = "@forelse($items as $item)\n{{ $item }}\n@empty\nNone\n@endforelse"
        out = preprocess(src)
        assert "{% for item in items %}" in out
        assert "{% else %}" in out
        assert "{% endfor %}" in out

    def test_forelse_without_empty(self):
        src = "@forelse($posts as $post)\n{{ $post }}\n@endforelse"
        out = preprocess(src)
        assert "{% for post in posts %}" in out
        assert "{% endfor %}" in out

    def test_forelse_independent_of_foreach(self):
        src = ("@foreach($a as $x){{ $x }}@endforeach\n"
               "@forelse($b as $y){{ $y }}@empty\nnone\n@endforelse")
        out = preprocess(src)
        assert out.count("{% for") == 2
        assert out.count("{% endfor %}") == 2


# ===========================================================================
# @switch / @case / @break / @default / @endswitch
# ===========================================================================

class TestSwitch:
    def test_switch_basic(self):
        src = (
            "@switch($status)\n"
            "    @case(1)\n"
            "        Active\n"
            "        @break\n"
            "    @case(2)\n"
            "        Inactive\n"
            "        @break\n"
            "    @default\n"
            "        Unknown\n"
            "@endswitch"
        )
        out = preprocess(src)
        assert "{% if status == 1 %}" in out
        assert "{% elif status == 2 %}" in out
        assert "{% else %}" in out
        assert "{% endif %}" in out
        assert "@break" not in out
        assert "@endswitch" not in out

    def test_switch_string_cases(self):
        src = (
            "@switch($role)\n"
            "    @case('admin')\n"
            "        Admin\n"
            "        @break\n"
            "    @case('user')\n"
            "        User\n"
            "        @break\n"
            "@endswitch"
        )
        out = preprocess(src)
        assert "{% if role == 'admin' %}" in out
        assert "{% elif role == 'user' %}" in out

    def test_switch_no_default(self):
        src = "@switch($x)\n@case(1)\nOne\n@break\n@endswitch"
        out = preprocess(src)
        assert "{% if x == 1 %}" in out
        assert "{% endif %}" in out
        assert "{% else %}" not in out

    def test_multiple_switches(self):
        src = (
            "@switch($a)\n@case(1)\nA\n@break\n@endswitch\n"
            "@switch($b)\n@case(2)\nB\n@break\n@endswitch"
        )
        out = preprocess(src)
        assert "{% if a == 1 %}" in out
        assert "{% if b == 2 %}" in out
        assert out.count("{% endif %}") == 2


# ===========================================================================
# @env / @endenv
# ===========================================================================

class TestEnv:
    def test_env_single(self):
        src = "@env('production')\nProd only\n@endenv"
        out = preprocess(src)
        assert "{% if _app_env == 'production' %}" in out
        assert "{% endif %}" in out

    def test_env_staging(self):
        src = "@env('staging')\nStaging\n@endenv"
        out = preprocess(src)
        assert "{% if _app_env == 'staging' %}" in out

    def test_env_array(self):
        src = "@env(['production', 'staging'])\nNot local\n@endenv"
        out = preprocess(src)
        assert "_app_env in" in out
        assert "'production'" in out
        assert "'staging'" in out
        assert "{% endif %}" in out

    def test_env_array_single_item(self):
        src = "@env(['local'])\nLocal only\n@endenv"
        out = preprocess(src)
        assert "{% if _app_env == 'local' %}" in out


# ===========================================================================
# @once / @endonce
# ===========================================================================

class TestOnce:
    def test_once_strips_markers(self):
        src = "@once\n<script>alert(1)</script>\n@endonce"
        out = preprocess(src)
        assert "@once" not in out
        assert "@endonce" not in out
        assert "<script>alert(1)</script>" in out

    def test_once_multiple_blocks(self):
        src = "@once\nFirst\n@endonce\n@once\nSecond\n@endonce"
        out = preprocess(src)
        assert "First" in out
        assert "Second" in out
        assert "@once" not in out


# ===========================================================================
# @py / @endpy
# ===========================================================================

class TestPy:
    def test_py_strips_markers(self):
        src = "@py\n{% set x = 42 %}\n@endpy"
        out = preprocess(src)
        assert "@py" not in out
        assert "@endpy" not in out
        assert "{% set x = 42 %}" in out


# ===========================================================================
# @prepend / @endprepend
# ===========================================================================

class TestPrepend:
    def test_prepend_treated_like_push(self):
        src = "@prepend('scripts')\n<script>...</script>\n@endprepend"
        out = preprocess(src)
        assert "@prepend" not in out
        assert "@endprepend" not in out
        assert "<script>...</script>" in out


# ===========================================================================
# @includeIf
# ===========================================================================

class TestIncludeIf:
    def test_include_if_basic(self):
        src = "@includeIf('partials.sidebar')"
        out = preprocess(src)
        assert "{% include 'partials/sidebar.html' ignore missing %}" in out

    def test_include_if_with_data_arg(self):
        src = "@includeIf('partials.nav', ['active' => 'home'])"
        out = preprocess(src)
        assert "ignore missing" in out
        assert "partials/nav.html" in out


# ===========================================================================
# @includeWhen
# ===========================================================================

class TestIncludeWhen:
    def test_include_when_basic(self):
        src = "@includeWhen($showAds, 'partials.ads')"
        out = preprocess(src)
        assert "{% if showAds %}" in out
        assert "partials/ads.html" in out
        assert "{% endif %}" in out

    def test_include_when_false_condition(self):
        src = "@includeWhen(false, 'partials.thing')"
        out = preprocess(src)
        assert "{% if false %}" in out


# ===========================================================================
# @includeFirst
# ===========================================================================

class TestIncludeFirst:
    def test_include_first_two_views(self):
        src = "@includeFirst(['custom.header', 'default.header'])"
        out = preprocess(src)
        assert "{% include" in out
        assert "custom/header.html" in out
        assert "default/header.html" in out
        assert "ignore missing" in out

    def test_include_first_single(self):
        src = "@includeFirst(['partials.nav'])"
        out = preprocess(src)
        assert "partials/nav.html" in out


# ===========================================================================
# Pipeline ordering — verify verbatim protects @-directives inside it
# ===========================================================================

class TestPipelineOrdering:
    def test_directives_inside_verbatim_not_processed(self):
        src = "@verbatim@if(true)yes@endif@endverbatim"
        out = preprocess(src)
        # These Blade directives should NOT be converted
        assert "@if(true)" in out
        assert "@endif" in out
        # But should be wrapped in raw
        assert "{% raw %}" in out

    def test_json_before_strip_dollar(self):
        # @json should produce {{ var | tojson }}, not {{ var  | tojson }} with double space
        src = "@json($myData)"
        out = preprocess(src)
        assert "{{ myData | tojson }}" in out

    def test_switch_inside_forelse(self):
        src = (
            "@forelse($items as $item)\n"
            "    @switch($item)\n"
            "        @case(1)One@break\n"
            "        @default Other\n"
            "    @endswitch\n"
            "@empty\n"
            "    No items\n"
            "@endforelse"
        )
        out = preprocess(src)
        assert "{% for item in items %}" in out
        assert "{% if item == 1 %}" in out
        assert "{% else %}" in out
        assert "{% endfor %}" in out


# ===========================================================================
# Existing directives still work (regression)
# ===========================================================================

class TestRegressions:
    def test_extends(self):
        out = preprocess("@extends('layout')")
        assert "{% extends 'layout.html' %}" in out

    def test_section(self):
        out = preprocess("@section('content')hello@endsection")
        assert "{% block content %}" in out

    def test_foreach(self):
        out = preprocess("@foreach($items as $item){{ $item }}@endforeach")
        assert "{% for item in items %}" in out

    def test_if(self):
        out = preprocess("@if($x)yes@endif")
        assert "{% if x %}" in out

    def test_can(self):
        out = preprocess("@can('edit')edit@endcan")
        assert "{% if can('edit') %}" in out

    def test_csrf(self):
        out = preprocess("@csrf")
        assert 'name="_token"' in out

    def test_auth(self):
        out = preprocess("@auth\nhello\n@endauth")
        assert "auth_user" in out

    def test_comment(self):
        out = preprocess("{{-- this is removed --}}")
        assert "this is removed" not in out
