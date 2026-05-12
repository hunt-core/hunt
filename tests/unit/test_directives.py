from hunt.view.directives import preprocess


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
