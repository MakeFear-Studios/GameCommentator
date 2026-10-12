"""Forward Python's standard ``logging`` onto the bus.

Libraries (the model client, capture, ...) report problems through ``logging``.
Forwarding them means everything ends up in the same session log.
"""

from __future__ import annotations

import logging

from .bus import EventBus

_LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class BusLogHandler(logging.Handler):
    """A ``logging.Handler`` that publishes records as ``stage="log"`` events."""

    def __init__(self, bus: EventBus, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = {"logger": record.name, "message": record.getMessage()}
            if record.exc_info:
                data["exception"] = logging.Formatter().formatException(record.exc_info)
            level = _LEVEL_NAMES.get(record.levelno, "error" if record.levelno > logging.ERROR else "info")
            self._bus.publish("log", "log_record", data, level)
        except Exception:
            self.handleError(record)


def capture_python_logging(bus: EventBus, level: int = logging.WARNING) -> BusLogHandler:
    """Attach a :class:`BusLogHandler` to the root logger. Returns it so it can be removed."""
    handler = BusLogHandler(bus, level)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler
