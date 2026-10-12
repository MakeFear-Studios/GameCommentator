"""Dev logger: writes every bus event of a session to ``logs/<session>/``.

::

    logs/2026-10-12_15-30-04/
    ├── events.jsonl   one JSON event per line
    ├── session.json   start/end time, app info, event and drop counts
    └── frames/        sampled frames, referenced from events by filename
"""

from __future__ import annotations

import json
import platform
import sys
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from .bus import EventBus, LogEvent
from .serialize import dumps

FORMAT_VERSION = 1


class DevLogger:
    """Subscribes to an :class:`EventBus` and writes a session folder.

    Use as a context manager, or call :meth:`start` and :meth:`stop`.
    """

    def __init__(
        self,
        bus: EventBus,
        root: str | Path = "logs",
        session_info: Mapping[str, Any] | None = None,
    ) -> None:
        self._bus = bus
        self._root = Path(root)
        self._session_info = dict(session_info or {})
        self._lock = threading.Lock()
        self._file: TextIO | None = None
        self._dir: Path | None = None
        self._started_at: datetime | None = None
        self._event_count = 0
        self._frame_count = 0

    @property
    def session_dir(self) -> Path:
        if self._dir is None:
            raise RuntimeError("logger has not been started")
        return self._dir

    # -- lifecycle --------------------------------------------------------

    def start(self) -> Path:
        """Create the session folder, start writing, and publish ``session_started``."""
        if self._file is not None:
            raise RuntimeError("logger already started")
        self._started_at = datetime.now().astimezone()
        self._dir = self._new_session_dir(self._started_at)
        (self._dir / "frames").mkdir()
        self._write_session_json(ended_at=None)
        # Line-buffered: every event reaches the OS as soon as it's written, so a crash keeps the log.
        self._file = open(self._dir / "events.jsonl", "w", encoding="utf-8", buffering=1)
        self._bus.subscribe(self._on_event)
        self._bus.publish(
            "logs",
            "session_started",
            {"format_version": FORMAT_VERSION, "session": self._dir.name, **self._session_info},
        )
        return self._dir

    def stop(self) -> None:
        """Publish ``session_ended``, deliver everything buffered, and close the files."""
        if self._file is None:
            return
        self._bus.publish("logs", "session_ended", {"dropped": self._bus.dropped})
        self._bus.flush()
        self._bus.unsubscribe(self._on_event)
        with self._lock:
            self._file.close()
            self._file = None
        self._write_session_json(ended_at=datetime.now().astimezone())

    def __enter__(self) -> DevLogger:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- frames -----------------------------------------------------------

    def save_frame(self, png: bytes, name: str | None = None) -> str:
        """Save an already-encoded PNG and return its path relative to the session folder.

        Put the returned path in an event's data, e.g. ``{"frame": "frames/000012.png"}``.
        """
        with self._lock:
            self._frame_count += 1
            filename = name or f"{self._frame_count:06d}.png"
        (self.session_dir / "frames" / filename).write_bytes(png)
        return f"frames/{filename}"

    # -- internals --------------------------------------------------------

    def _on_event(self, event: LogEvent) -> None:
        line = dumps(event.to_dict())
        with self._lock:
            if self._file is None:
                return
            self._file.write(line + "\n")
            self._event_count += 1

    def _new_session_dir(self, when: datetime) -> Path:
        base = when.strftime("%Y-%m-%d_%H-%M-%S")
        self._root.mkdir(parents=True, exist_ok=True)
        candidate = self._root / base
        n = 2
        while candidate.exists():
            candidate = self._root / f"{base}_{n}"
            n += 1
        candidate.mkdir()
        return candidate

    def _write_session_json(self, ended_at: datetime | None) -> None:
        assert self._dir is not None and self._started_at is not None
        info = {
            "format_version": FORMAT_VERSION,
            "session": self._dir.name,
            "started_at": self._started_at.isoformat(),
            "ended_at": ended_at.isoformat() if ended_at else None,
            "events_written": self._event_count,
            "events_dropped": self._bus.dropped,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "app": self._session_info,
        }
        text = json.dumps(info, indent=2, default=str, ensure_ascii=False)
        (self._dir / "session.json").write_text(text + "\n", encoding="utf-8")
