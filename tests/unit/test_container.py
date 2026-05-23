import pytest

from hunt.container.container import BindingResolutionError, Container


def test_bind_and_make():
    c = Container()
    c.bind("greeting", lambda: "hello")
    assert c.make("greeting") == "hello"


def test_singleton_returns_same_instance():
    c = Container()

    class Counter:
        count = 0
        def __init__(self):
            Counter.count += 1

    c.singleton("counter", Counter)
    a = c.make("counter")
    b = c.make("counter")
    assert a is b
    assert Counter.count == 1


def test_instance():
    c = Container()
    obj = object()
    c.instance("thing", obj)
    assert c.make("thing") is obj


def test_raises_on_unbound():
    c = Container()
    with pytest.raises(BindingResolutionError):
        c.make("does_not_exist")


def test_auto_resolve_class():
    c = Container()

    class Service:
        pass

    result = c.make(Service)
    assert isinstance(result, Service)
