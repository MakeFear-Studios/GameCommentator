"""Frame type, newest-frame buffer and the backend interface."""

import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Frame:
    image: np.ndarray  # height x width x 4, BGRA, uint8
    t: float = field(default_factory=time.monotonic)

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]


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
