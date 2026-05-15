from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any


class _LogManager:
    """Thin wrapper around Python's stdlib logging with multi-channel support."""

    _logger: logging.Logger | None = None

    def configure(
        self, log_path: Path, level: str = "debug", max_bytes: int = 10_485_760, backup_count: int = 5
    ) -> None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("hunt")
        logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))
        logger.handlers.clear()

        # Rotating file handler
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)-8s %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(fh)

        # Stderr handler in debug mode
        if os.environ.get("APP_DEBUG", "false").lower() == "true":
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
            logger.addHandler(sh)

        self._logger = logger

    def _get(self) -> logging.Logger:
        if self._logger is None:
            return logging.getLogger("hunt")
        return self._logger

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


Log = _LogManager()
