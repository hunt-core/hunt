"""Regression tests for LOG-01 and LOG-L1."""

from __future__ import annotations

import logging
import logging.handlers

import pytest

from hunt.log import manager as log_manager
from hunt.log.manager import _build_channel, _close_handlers, _LogManager


@pytest.fixture
def mgr(monkeypatch):
    # Synchronous handlers: what is written is on disk as soon as log() returns.
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    m = _LogManager()
    yield m
    for logger in m._channels.values():
        _close_handlers(logger)


def _read(path):
    return path.read_text() if path.exists() else ""


class TestStackLevels:
    def test_member_level_is_honoured_inside_a_stack(self, mgr, tmp_path):
        mgr.configure(
            channels={
                "lg1_all": {"driver": "file", "path": str(tmp_path / "all.log"), "level": "debug"},
                "lg1_warn": {"driver": "file", "path": str(tmp_path / "warn.log"), "level": "warning"},
                "lg1_stack": {"driver": "stack", "channels": ["lg1_all", "lg1_warn"]},
            },
            default="lg1_stack",
        )
        mgr.debug("debug-line")
        mgr.info("info-line")
        mgr.warning("warning-line")
        mgr.error("error-line")

        everything = _read(tmp_path / "all.log")
        warnings = _read(tmp_path / "warn.log")
        for line in ("debug-line", "info-line", "warning-line", "error-line"):
            assert line in everything
        assert "debug-line" not in warnings
        assert "info-line" not in warnings
        assert "warning-line" in warnings
        assert "error-line" in warnings

    def test_stderr_member_at_warning_drops_debug(self, mgr, tmp_path, capsys):
        mgr.configure(
            channels={
                "lg2_file": {"driver": "file", "path": str(tmp_path / "app.log"), "level": "debug"},
                "lg2_stderr": {"driver": "stderr", "level": "warning"},
                "lg2_stack": {"driver": "stack", "channels": ["lg2_file", "lg2_stderr"]},
            },
            default="lg2_stack",
        )
        mgr.debug("quiet-debug")
        mgr.warning("loud-warning")
        err = capsys.readouterr().err
        assert "quiet-debug" not in err
        assert "loud-warning" in err
        assert "quiet-debug" in _read(tmp_path / "app.log")

    def test_stack_level_filters_before_fan_out(self, mgr, tmp_path):
        mgr.configure(
            channels={
                "lg3_file": {"driver": "file", "path": str(tmp_path / "app.log"), "level": "debug"},
                "lg3_stack": {"driver": "stack", "channels": ["lg3_file"], "level": "error"},
            },
            default="lg3_stack",
        )
        mgr.warning("below-stack-level")
        mgr.error("at-stack-level")
        content = _read(tmp_path / "app.log")
        assert "below-stack-level" not in content
        assert "at-stack-level" in content

    def test_levels_are_set_on_handlers(self, mgr, tmp_path):
        mgr.configure(
            channels={
                "lg4_file": {"driver": "file", "path": str(tmp_path / "a.log"), "level": "error"},
                "lg4_daily": {"driver": "daily", "path": str(tmp_path / "d.log"), "level": "warning"},
                "lg4_err": {"driver": "stderr", "level": "info"},
            }
        )
        assert [h.level for h in mgr._channels["lg4_file"].handlers] == [logging.ERROR]
        assert [h.level for h in mgr._channels["lg4_daily"].handlers] == [logging.WARNING]
        assert [h.level for h in mgr._channels["lg4_err"].handlers] == [logging.INFO]

    def test_direct_channel_still_filters_by_level(self, mgr, tmp_path):
        mgr.configure(channels={"lg5": {"driver": "file", "path": str(tmp_path / "a.log"), "level": "warning"}})
        mgr.channel("lg5").info("hidden")
        mgr.channel("lg5").warning("shown")
        content = _read(tmp_path / "a.log")
        assert "hidden" not in content and "shown" in content


class TestStackConstruction:
    def test_stack_declared_before_its_members(self, mgr, tmp_path):
        mgr.configure(
            channels={
                "lg6_stack": {"driver": "stack", "channels": ["lg6_file"]},
                "lg6_file": {"driver": "file", "path": str(tmp_path / "app.log")},
            },
            default="lg6_stack",
        )
        assert len(mgr._channels["lg6_stack"].handlers) == 1
        mgr.info("reaches-the-file")
        assert "reaches-the-file" in _read(tmp_path / "app.log")
        # Declaration order is kept, so the implicit default is still the first channel.
        assert list(mgr._channels) == ["lg6_stack", "lg6_file"]

    def test_stack_of_stacks(self, mgr, tmp_path):
        mgr.configure(
            channels={
                "lg7_outer": {"driver": "stack", "channels": ["lg7_inner"]},
                "lg7_inner": {"driver": "stack", "channels": ["lg7_file"]},
                "lg7_file": {"driver": "file", "path": str(tmp_path / "app.log")},
            },
            default="lg7_outer",
        )
        mgr.info("nested")
        assert "nested" in _read(tmp_path / "app.log")

    def test_unknown_member_raises(self, mgr, tmp_path):
        with pytest.raises(ValueError, match="unknown channel 'lg8_missing'"):
            mgr.configure(
                channels={
                    "lg8_file": {"driver": "file", "path": str(tmp_path / "app.log")},
                    "lg8_stack": {"driver": "stack", "channels": ["lg8_file", "lg8_missing"]},
                }
            )

    def test_empty_stack_raises(self, mgr):
        with pytest.raises(ValueError, match="no channels"):
            mgr.configure(channels={"lg9_stack": {"driver": "stack", "channels": []}})

    def test_cyclic_stacks_raise(self, mgr):
        with pytest.raises(ValueError, match="cycle"):
            mgr.configure(
                channels={
                    "lg10_a": {"driver": "stack", "channels": ["lg10_b"]},
                    "lg10_b": {"driver": "stack", "channels": ["lg10_a"]},
                }
            )

    def test_build_channel_stack_without_known_members_raises(self):
        with pytest.raises(ValueError, match="unknown channel"):
            _build_channel("lg11_stack", {"driver": "stack", "channels": ["lg11_never_built"]})


class TestReconfigure:
    def test_handlers_are_closed_on_reconfigure(self, mgr, tmp_path):
        mgr.configure(channels={"lg12": {"driver": "file", "path": str(tmp_path / "a.log")}})
        logger = mgr._channels["lg12"]
        (old,) = logger.handlers
        assert old.stream is not None
        mgr.configure(channels={"lg12": {"driver": "file", "path": str(tmp_path / "b.log")}})
        assert old.stream is None  # closed, not just dropped
        assert old not in logger.handlers
        assert len(logger.handlers) == 1

    def test_build_channel_closes_previous_handlers(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APP_ENV", "testing")
        logger = _build_channel("lg13", {"driver": "file", "path": str(tmp_path / "a.log")})
        (old,) = logger.handlers
        _build_channel("lg13", {"driver": "file", "path": str(tmp_path / "a.log")})
        assert old.stream is None
        _close_handlers(logger)

    def test_reconfigure_replaces_the_channel_set(self, mgr, tmp_path):
        mgr.configure(
            channels={
                "lg14_old": {"driver": "file", "path": str(tmp_path / "old.log")},
                "lg14_keep": {"driver": "file", "path": str(tmp_path / "keep.log")},
            }
        )
        old_logger = mgr._channels["lg14_old"]
        mgr.configure(channels={"lg14_keep": {"driver": "file", "path": str(tmp_path / "keep.log")}})
        with pytest.raises(RuntimeError):
            mgr.channel("lg14_old")
        assert old_logger.handlers == []
        mgr.channel("lg14_keep").info("still-works")
        assert "still-works" in _read(tmp_path / "keep.log")

    def test_switch_from_channels_to_single(self, mgr, tmp_path):
        mgr.configure(channels={"lg15": {"driver": "file", "path": str(tmp_path / "a.log")}})
        mgr.configure(log_path=tmp_path / "single.log", level="info")
        assert list(mgr._channels) == ["single"]
        mgr.info("single-line")
        assert "single-line" in _read(tmp_path / "single.log")


class TestListeners:
    @pytest.fixture
    def threaded(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "local")
        monkeypatch.setenv("LOG_NON_BLOCKING", "true")
        # A private registry, so stopping listeners here never touches ones
        # other tests may have left running.
        monkeypatch.setattr(log_manager, "_listeners", [])
        yield
        log_manager._stop_listeners()

    def test_rebuilding_one_channel_keeps_other_listeners_running(self, threaded, tmp_path):
        a = _build_channel("lg16_a", {"driver": "file", "path": str(tmp_path / "a.log")})
        b = _build_channel("lg16_b", {"driver": "file", "path": str(tmp_path / "b.log")})
        listener_a = a.handlers[0]._hunt_listener
        listener_b = b.handlers[0]._hunt_listener
        assert isinstance(a.handlers[0], logging.handlers.QueueHandler)

        a = _build_channel("lg16_a", {"driver": "file", "path": str(tmp_path / "a.log")})

        assert listener_a not in log_manager._listeners
        assert listener_a._thread is None  # stopped
        assert listener_a.handlers[0].stream is None  # and its file handler closed
        assert listener_b in log_manager._listeners
        assert listener_b._thread is not None and listener_b._thread.is_alive()

        b.info("b-still-logs")
        log_manager._stop_listener(listener_b)  # flushes the queue
        assert "b-still-logs" in _read(tmp_path / "b.log")

    def test_reconfigure_flushes_and_restarts_listeners(self, threaded, tmp_path):
        m = _LogManager()
        m.configure(channels={"lg17": {"driver": "file", "path": str(tmp_path / "a.log")}})
        first = m._channels["lg17"].handlers[0]._hunt_listener
        m.info("before-reconfigure")
        m.configure(channels={"lg17": {"driver": "file", "path": str(tmp_path / "a.log")}})
        second = m._channels["lg17"].handlers[0]._hunt_listener
        assert first is not second
        assert first._thread is None
        assert second._thread.is_alive()
        assert "before-reconfigure" in _read(tmp_path / "a.log")
        m.info("after-reconfigure")
        _close_handlers(m._channels["lg17"])
        assert "after-reconfigure" in _read(tmp_path / "a.log")

    def test_queue_handler_carries_the_channel_level(self, threaded, tmp_path):
        logger = _build_channel("lg18", {"driver": "file", "path": str(tmp_path / "a.log"), "level": "error"})
        assert logger.handlers[0].level == logging.ERROR
        assert logger.handlers[0]._hunt_listener.handlers[0].level == logging.ERROR
        _close_handlers(logger)

    def test_stop_listeners_stops_everything(self, threaded, tmp_path):
        logger = _build_channel("lg19", {"driver": "file", "path": str(tmp_path / "a.log")})
        listener = logger.handlers[0]._hunt_listener
        log_manager._stop_listeners()
        assert log_manager._listeners == []
        assert listener._thread is None
        assert listener.handlers[0].stream is None
        _close_handlers(logger)
