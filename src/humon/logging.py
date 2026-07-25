"""Structured logging (FR-1.4).

Emits one JSON object per line to stdout (captured by journald under systemd),
or a human-readable line format for local runs. ``get_logger`` returns a small
adapter whose ``info``/``warning``/etc. accept arbitrary structured fields.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_CONFIGURED = False

# Standard LogRecord attributes we don't want to echo as "extra" fields.
_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Idempotent within a process."""

    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    _CONFIGURED = True


class StructuredLogger:
    """Thin wrapper letting callers pass structured fields as kwargs."""

    def __init__(self, name: str) -> None:
        self._log = logging.getLogger(name)

    def _emit(self, level: int, msg: str, **fields: Any) -> None:
        self._log.log(level, msg, extra=fields)

    def debug(self, msg: str, **f: Any) -> None:
        self._emit(logging.DEBUG, msg, **f)

    def info(self, msg: str, **f: Any) -> None:
        self._emit(logging.INFO, msg, **f)

    def warning(self, msg: str, **f: Any) -> None:
        self._emit(logging.WARNING, msg, **f)

    def error(self, msg: str, **f: Any) -> None:
        self._emit(logging.ERROR, msg, **f)

    def exception(self, msg: str, **f: Any) -> None:
        self._log.exception(msg, extra=f)


def get_logger(name: str) -> StructuredLogger:
    if not _CONFIGURED:
        configure_logging()
    return StructuredLogger(name)
