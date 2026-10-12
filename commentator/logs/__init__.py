"""Logging: the log event bus, dev logger and log reader.

Stages publish what they're doing::

    from commentator.logs import bus

    bus.publish("director", "event_dropped", {"event": "healed", "reason": "expired"})

In dev mode a :class:`DevLogger` writes everything to ``logs/<session>/events.jsonl``.
With no subscribers, ``publish`` is effectively free.
"""

from .bus import LEVELS, EventBus, LogEvent
from .dev_logger import FORMAT_VERSION, DevLogger
from .python_logging import BusLogHandler, capture_python_logging
from .reader import read_log
from .session import default_log_root, dev_mode_from_env, dev_session, is_frozen

bus = EventBus()
"""The app-wide bus every stage publishes to."""

__all__ = [
    "FORMAT_VERSION",
    "LEVELS",
    "BusLogHandler",
    "DevLogger",
    "EventBus",
    "LogEvent",
    "bus",
    "capture_python_logging",
    "default_log_root",
    "dev_mode_from_env",
    "dev_session",
    "is_frozen",
    "read_log",
]
