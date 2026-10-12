"""Dev-mode session: everything ``--dev`` needs, in one context manager.

Used by ``main.py``; works the same from source and from the packaged .exe.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .bus import EventBus
from .dev_logger import DevLogger
from .python_logging import capture_python_logging

APP_DIR_NAME = "GameCommentator"
DEV_ENV_VAR = "GAME_COMMENTATOR_DEV"
LOG_DIR_ENV_VAR = "GAME_COMMENTATOR_LOG_DIR"


def is_frozen() -> bool:
    """True when running as the PyInstaller-built .exe."""
    return bool(getattr(sys, "frozen", False))


def dev_mode_from_env() -> bool:
    """``GAME_COMMENTATOR_DEV=1`` turns on dev mode, e.g. for the windowed .exe where passing flags is awkward."""
    return os.environ.get(DEV_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def default_log_root() -> Path:
    """Where session folders go.

    - ``GAME_COMMENTATOR_LOG_DIR`` if set.
    - Packaged .exe: ``%LOCALAPPDATA%\\GameCommentator\\logs`` (Program Files isn't writable).
    - From source: ``<repo>/logs`` (git-ignored), wherever you run it from.

    TODO: move into ``core/paths.py`` once task 0.7 adds the shared paths helper.
    """
    override = os.environ.get(LOG_DIR_ENV_VAR)
    if override:
        return Path(override)
    if is_frozen():
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / APP_DIR_NAME / "logs"
    return Path(__file__).resolve().parents[2] / "logs"


def _error_data(exc: BaseException) -> dict[str, Any]:
    return {
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }


@contextmanager
def dev_session(
    bus: EventBus,
    root: str | Path | None = None,
    session_info: Mapping[str, Any] | None = None,
    python_log_level: int = logging.WARNING,
) -> Iterator[DevLogger]:
    """Record everything published on ``bus`` for the duration of the ``with`` block.

    - Starts a :class:`DevLogger` in ``root`` (default: :func:`default_log_root`).
    - Forwards Python ``logging`` (``python_log_level`` and above) onto the bus.
    - Records crashes: an exception escaping the block (``app / crashed``, then re-raised),
      Ctrl+C (``app / interrupted``), and exceptions in background threads (``app / thread_crashed``).
    - Undoes all of that on exit, so nothing leaks into the next session.
    """
    info = {"frozen": is_frozen(), **(session_info or {})}
    logger = DevLogger(bus, root=root if root is not None else default_log_root(), session_info=info)
    logger.start()
    handler = capture_python_logging(bus, level=python_log_level)
    previous_thread_hook = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            data = _error_data(args.exc_value)
            data["thread"] = args.thread.name if args.thread else None
            bus.publish("app", "thread_crashed", data, level="error")
        previous_thread_hook(args)

    threading.excepthook = thread_hook
    try:
        yield logger
    except KeyboardInterrupt:
        bus.publish("app", "interrupted", {"reason": "keyboard_interrupt"})
        raise
    except BaseException as exc:
        bus.publish("app", "crashed", _error_data(exc), level="error")
        raise
    finally:
        threading.excepthook = previous_thread_hook
        logging.getLogger().removeHandler(handler)
        logger.stop()
