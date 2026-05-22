from __future__ import annotations

import datetime
import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any


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

        ts = datetime.datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "request_id": rid,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_channel(name: str, config: dict, base_path: Path | None = None) -> logging.Logger:
    """Build and return a named stdlib logger from a channel config dict."""
    logger = logging.getLogger(f"hunt.{name}")
    logger.handlers.clear()
    logger.propagate = False

    driver = config.get("driver", "file")
    level_str = config.get("level", os.environ.get("LOG_LEVEL", "debug"))
    level = getattr(logging, level_str.upper(), logging.DEBUG)
    logger.setLevel(level)

    use_json = os.environ.get("LOG_FORMAT", "text").lower() == "json"

    if driver == "file":
        log_path = config.get("path")
        if log_path is None and base_path is not None:
            log_path = base_path / "storage" / "logs" / "hunt.log"
        if log_path is None:
            log_path = Path("storage/logs/hunt.log")
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = config.get("max_bytes", 10_485_760)
        backup_count = config.get("backup_count", 5)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        handler.setFormatter(_JsonFormatter() if use_json else _make_formatter())
        logger.addHandler(handler)

    elif driver == "daily":
        log_path = config.get("path")
        if log_path is None and base_path is not None:
            log_path = base_path / "storage" / "logs" / "hunt.log"
        if log_path is None:
            log_path = Path("storage/logs/hunt.log")
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        days = config.get("days", 7)
        handler = logging.handlers.TimedRotatingFileHandler(
            log_path, when="midnight", backupCount=days, encoding="utf-8"
        )
        handler.setFormatter(_JsonFormatter() if use_json else _make_formatter())
        logger.addHandler(handler)

    elif driver == "stderr":
        handler = logging.StreamHandler()
        stderr_fmt = _JsonFormatter() if use_json else _make_formatter("%(levelname)-8s %(message)s")
        handler.setFormatter(stderr_fmt)
        logger.addHandler(handler)

    elif driver == "stack":
        # Fan out to each named channel listed in config["channels"]
        # The stack channel itself has no handlers — output flows via propagation
        # to sub-loggers, so we attach sub-loggers via a custom handler instead.
        channels = config.get("channels", [])
        for ch_name in channels:
            sub = logging.getLogger(f"hunt.{ch_name}")
            if sub.handlers:
                logger.addHandler(_PropagateHandler(sub))

    else:
        # Unknown driver — fall back to stderr
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter() if use_json else _make_formatter("%(levelname)-8s %(message)s"))
        logger.addHandler(handler)

    return logger


class _PropagateHandler(logging.Handler):
    """Forward records to a specific logger."""

    def __init__(self, target: logging.Logger) -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
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
        level: str = "debug",
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
        """
        if channels:
            for name, cfg in channels.items():
                self._channels[name] = _build_channel(name, cfg, base_path=base_path)
            self._default = default or next(iter(channels))
            self._default_logger = self._channels[self._default]
        else:
            # Backward-compatible single-channel setup
            cfg = {
                "driver": "file",
                "path": log_path,
                "level": level,
                "max_bytes": max_bytes,
                "backup_count": backup_count,
            }
            logger = _build_channel("single", cfg, base_path=base_path)
            # Also echo to stderr when APP_DEBUG=true
            if os.environ.get("APP_DEBUG", "false").lower() == "true":
                sh = logging.StreamHandler()
                sh.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
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
        self._get().exception(message, exc_info=exc)

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
        self._logger.exception(message, exc_info=exc)


Log = _LogManager()
