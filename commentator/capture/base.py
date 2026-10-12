"""Newest-frame buffer and the backend interface."""

import threading
from typing import Protocol

from commentator.core.types import Frame


class LatestFrame:
    """Holds only the newest frame. Writers overwrite; readers never fall behind."""

    def __init__(self) -> None:
        self._frame: Frame | None = None
        self._count = 0
        self._lock = threading.Lock()

    def put(self, frame: Frame) -> None:
        with self._lock:
            self._frame = frame
            self._count += 1

    def get(self) -> Frame | None:
        with self._lock:
            return self._frame

    @property
    def count(self) -> int:
        """Frames received so far (including overwritten ones)."""
        with self._lock:
            return self._count


class CaptureBackend(Protocol):
    name: str

    def start(self, hwnd: int) -> None: ...

    def stop(self) -> None: ...

    def latest(self) -> Frame | None: ...

    @property
    def frame_count(self) -> int: ...
