from __future__ import annotations

import atexit
import datetime
import json
import logging
import logging.handlers
import os
import queue
from pathlib import Path
from typing import Any

# Background listeners owning the real (blocking) file handlers when logging is
# run off-thread. Tracked so they can be flushed/stopped on reconfigure + exit.
_listeners: list[logging.handlers.QueueListener] = []
_atexit_registered = False


def _stop_listener(listener: logging.handlers.QueueListener) -> None:
    """Flush and stop one listener, then close the real handlers it owns."""
    try:
        _listeners.remove(listener)
    except ValueError:
        pass
    try:
        listener.stop()
    except Exception:
        pass
    for handler in listener.handlers:
        try:
            handler.close()
        except Exception:
            pass


def _stop_listeners() -> None:
    """Flush and stop all background log listeners."""
    while _listeners:
        _stop_listener(_listeners[-1])


def _close_handlers(logger: logging.Logger) -> None:
    """Detach and close every handler of *logger*.

    A queue handler's background listener (and the file handler behind it) is
    stopped and closed too.  Only this logger's listeners are touched, so
    rebuilding one channel never silences another.
    """
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        listener = getattr(handler, "_hunt_listener", None)
        if listener is not None:
            _stop_listener(listener)
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass


def _maybe_non_blocking(handler: logging.Handler) -> logging.Handler:
    """Move file I/O for ``handler`` onto a background thread via a queue.

    Synchronous file writes in a request handler block the async event loop;
    routing the records through a ``QueueHandler`` lets the loop continue while a
    listener thread does the disk write. Disabled under APP_ENV=testing (to avoid
    thread churn across tests) and when LOG_NON_BLOCKING=false.
    """
    if os.environ.get("APP_ENV", "local") == "testing":
        return handler
    if os.environ.get("LOG_NON_BLOCKING", "true").lower() == "false":
        return handler

    global _atexit_registered
    q: queue.Queue = queue.Queue(-1)
    listener = logging.handlers.QueueListener(q, handler, respect_handler_level=True)
    listener.start()
    _listeners.append(listener)
    if not _atexit_registered:
        atexit.register(_stop_listeners)
        _atexit_registered = True
    queue_handler = logging.handlers.QueueHandler(q)
    queue_handler.setLevel(handler.level)
    # Lets _close_handlers stop exactly this channel's listener.
    queue_handler._hunt_listener = listener  # type: ignore[attr-defined]
    return queue_handler


def _make_formatter(fmt: str = "[%(asctime)s] %(levelname)-8s %(message)s") -> logging.Formatter:
    return logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON object (one per line)."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            from hunt.ctx import request_id as _request_id_var

            rid = _request_id_var.get() or None
        except Exception:
            rid = None

        ts = datetime.datetime.fromtimestamp(record.created, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "request_id": rid,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _channel_level(config: dict) -> int:
    level_str = config.get("level") or os.environ.get("LOG_LEVEL", "debug")
    level = getattr(logging, str(level_str).upper(), logging.DEBUG)
    return level if isinstance(level, int) else logging.DEBUG


def _build_channel(
    name: str,
    config: dict,
    base_path: Path | None = None,
    channels: dict[str, logging.Logger] | None = None,
) -> logging.Logger:
    """Build and return a named stdlib logger from a channel config dict.

    The channel level is set on the logger *and* on each of its handlers, so
    it still applies when the channel receives records through a ``stack``.

    For a ``stack`` channel, *channels* maps the already-built channel names it
    may fan out to; a member missing from it raises ``ValueError``.

    Note: the ``file`` driver rotates by size and must be written by a single
    process per file (several processes rotating the same file corrupt it).
    With multiple workers give each its own path, or use the ``daily`` or
    ``stderr`` driver and let the process manager collect the output.
    """
    logger = logging.getLogger(f"hunt.{name}")
    _close_handlers(logger)
    logger.propagate = False

    driver = config.get("driver", "file")
    level = _channel_level(config)
    logger.setLevel(level)

    use_json = os.environ.get("LOG_FORMAT", "text").lower() == "json"

    if driver == "file":
        log_path = config.get("path")
        if log_path is None and base_path is not None:
            log_path = base_path / "storage" / "logs" / "hunt.log"
        if log_path is None:
            log_path = Path("storage/logs/hunt.log")
        log_path = Path(log_path)
        if not log_path.is_absolute() and base_path is not None:
            log_path = base_path / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = config.get("max_bytes", 10_485_760)
        backup_count = config.get("backup_count", 5)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        handler.setFormatter(_JsonFormatter() if use_json else _make_formatter())
        handler.setLevel(level)
        logger.addHandler(_maybe_non_blocking(handler))

    elif driver == "daily":
        log_path = config.get("path")
        if log_path is None and base_path is not None:
            log_path = base_path / "storage" / "logs" / "hunt.log"
        if log_path is None:
            log_path = Path("storage/logs/hunt.log")
        log_path = Path(log_path)
        if not log_path.is_absolute() and base_path is not None:
            log_path = base_path / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        days = config.get("days", 7)
        handler = logging.handlers.TimedRotatingFileHandler(
            log_path, when="midnight", backupCount=days, encoding="utf-8"
        )
        handler.setFormatter(_JsonFormatter() if use_json else _make_formatter())
        handler.setLevel(level)
        logger.addHandler(_maybe_non_blocking(handler))

    elif driver == "stderr":
        handler = logging.StreamHandler()
        stderr_fmt = _JsonFormatter() if use_json else _make_formatter("%(levelname)-8s %(message)s")
        handler.setFormatter(stderr_fmt)
        handler.setLevel(level)
        logger.addHandler(handler)

    elif driver == "stack":
        # Fan out to each named channel listed in config["channels"] through a
        # forwarding handler per member. Each member's handlers carry that
        # member's level, so e.g. a "warning" stderr member drops debug records.
        members = config.get("channels", [])
        if not members:
            raise ValueError(f"Log stack channel '{name}' lists no channels.")
        for ch_name in members:
            if ch_name == name:
                raise ValueError(f"Log stack channel '{name}' cannot contain itself.")
            sub = channels.get(ch_name) if channels is not None else None
            if sub is None:
                raise ValueError(f"Log stack channel '{name}' references unknown channel '{ch_name}'.")
            forward = _PropagateHandler(sub)
            forward.setLevel(level)
            logger.addHandler(forward)

    else:
        # Unknown driver — fall back to stderr
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter() if use_json else _make_formatter("%(levelname)-8s %(message)s"))
        handler.setLevel(level)
        logger.addHandler(handler)

    return logger


def _build_channels(configs: dict[str, dict], base_path: Path | None = None) -> dict[str, logging.Logger]:
    """Build every channel: plain channels first, then stacks once their members exist.

    Definition order does not matter.  A stack naming a channel that is not
    configured (or stacks that only reference each other) raises ``ValueError``.
    """
    built: dict[str, logging.Logger] = {}
    stacks: dict[str, dict] = {}
    for name, cfg in configs.items():
        if cfg.get("driver", "file") == "stack":
            stacks[name] = cfg
        else:
            built[name] = _build_channel(name, cfg, base_path=base_path)

    for name, cfg in stacks.items():
        for member in cfg.get("channels", []):
            if member not in configs:
                raise ValueError(f"Log stack channel '{name}' references unknown channel '{member}'.")

    pending = dict(stacks)
    while pending:
        ready = [n for n, cfg in pending.items() if all(m in built for m in cfg.get("channels", []))]
        if not ready:
            raise ValueError(f"Log stack channels reference each other in a cycle: {', '.join(sorted(pending))}.")
        for name in ready:
            built[name] = _build_channel(name, pending.pop(name), base_path=base_path, channels=built)

    # Keep the order the channels were declared in.
    return {name: built[name] for name in configs}


class _PropagateHandler(logging.Handler):
    """Forward records to a specific logger, honouring that logger's level."""

    def __init__(self, target: logging.Logger) -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        # Logger.handle() skips the logger's own level check, so apply it here.
        if record.levelno >= self._target.getEffectiveLevel():
            self._target.handle(record)


class _LogManager:
    """Structured logging manager with multi-channel support."""

    def __init__(self) -> None:
        self._channels: dict[str, logging.Logger] = {}
        self._default: str = "single"
        self._default_logger: logging.Logger | None = None

    def configure(
        self,
        log_path: Path | str | None = None,
        level: str | None = None,
        max_bytes: int = 10_485_760,
        backup_count: int = 5,
        channels: dict[str, dict] | None = None,
        default: str | None = None,
        base_path: Path | None = None,
    ) -> None:
        """Configure the log manager.

        Simple usage (single rotating file, backward-compatible)::

            Log.configure(log_path="storage/logs/hunt.log", level="info")

        Multi-channel usage::

            Log.configure(
                channels={
                    "file":   {"driver": "file",   "path": "storage/logs/app.log", "level": "debug"},
                    "daily":  {"driver": "daily",  "path": "storage/logs/app.log", "days": 14},
                    "stderr": {"driver": "stderr", "level": "warning"},
                    "stack":  {"driver": "stack",  "channels": ["file", "stderr"]},
                },
                default="stack",
            )

        Channel levels apply inside a stack too (``stderr`` above only gets
        warnings).  A stack may be declared before its members; naming an
        unknown channel raises ``ValueError``.  Calling ``configure()`` again
        replaces all channels and closes the previous handlers.

        The ``file`` driver must have one writing process per file; see
        ``_build_channel``.
        """
        # Reconfiguring replaces the whole channel set: close the handlers (and
        # stop the background listeners) of every channel this manager built,
        # then rebuild from the new config.
        for old in self._channels.values():
            _close_handlers(old)
        self._channels = {}
        self._default_logger = None
        if channels:
            self._channels = _build_channels(channels, base_path=base_path)
            if default not in self._channels:
                default = None
            self._default = default or next(iter(channels))
            self._default_logger = self._channels[self._default]
        else:
            # Backward-compatible single-channel setup. The channel driver
            # comes from LOG_CHANNEL ("file", "daily" or "stderr"); "stack"
            # needs a channels dict, so it falls back to "file" here.
            driver = os.environ.get("LOG_CHANNEL", "file")
            if driver == "stack":
                driver = "file"
            cfg = {
                "driver": driver,
                "path": log_path,
                "level": level or os.environ.get("LOG_LEVEL", "debug"),
                "max_bytes": max_bytes,
                "backup_count": backup_count,
            }
            logger = _build_channel("single", cfg, base_path=base_path)
            # Also echo to stderr when APP_DEBUG=true
            if os.environ.get("APP_DEBUG", "false").lower() == "true":
                sh = logging.StreamHandler()
                sh.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
                sh.setLevel(logger.level)
                logger.addHandler(sh)
            self._channels["single"] = logger
            self._default = "single"
            self._default_logger = logger

    def channel(self, name: str) -> _ChannelProxy:
        """Return a proxy that writes to the named channel."""
        logger = self._channels.get(name)
        if logger is None:
            raise RuntimeError(f"Log channel '{name}' is not configured.")
        return _ChannelProxy(logger)

    def _get(self) -> logging.Logger:
        if self._default_logger is None and os.environ.get("LOG_CHANNEL"):
            # Unconfigured but LOG_CHANNEL is set: build the channel from env.
            # Without LOG_CHANNEL we keep the bare fallback logger so tests
            # and scripts never create storage/logs as a side effect.
            self.configure()
        if self._default_logger is not None:
            return self._default_logger
        return logging.getLogger("hunt")

    def debug(self, message: str, **context: Any) -> None:
        self._get().debug(self._format(message, context))

    def info(self, message: str, **context: Any) -> None:
        self._get().info(self._format(message, context))

    def warning(self, message: str, **context: Any) -> None:
        self._get().warning(self._format(message, context))

    def error(self, message: str, **context: Any) -> None:
        self._get().error(self._format(message, context))

    def critical(self, message: str, **context: Any) -> None:
        self._get().critical(self._format(message, context))

    def exception(self, message: str, exc: Exception | None = None) -> None:
        self._get().exception(message, exc_info=exc if exc is not None else True)

    @staticmethod
    def _format(message: str, context: dict) -> str:
        safe_msg = message.replace("\r", "\\r").replace("\n", "\\n")
        if context:
            parts = []
            for k, v in context.items():
                safe_k = str(k).replace("\r", "\\r").replace("\n", "\\n")
                parts.append(f"{safe_k}={v!r}")
            return f"{safe_msg} | {' '.join(parts)}"
        return safe_msg


class _ChannelProxy:
    """Thin proxy that writes to a specific logging.Logger."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def debug(self, message: str, **context: Any) -> None:
        self._logger.debug(_LogManager._format(message, context))

    def info(self, message: str, **context: Any) -> None:
        self._logger.info(_LogManager._format(message, context))

    def warning(self, message: str, **context: Any) -> None:
        self._logger.warning(_LogManager._format(message, context))

    def error(self, message: str, **context: Any) -> None:
        self._logger.error(_LogManager._format(message, context))

    def critical(self, message: str, **context: Any) -> None:
        self._logger.critical(_LogManager._format(message, context))

    def exception(self, message: str, exc: Exception | None = None) -> None:
        self._logger.exception(message, exc_info=exc if exc is not None else True)


Log = _LogManager()
