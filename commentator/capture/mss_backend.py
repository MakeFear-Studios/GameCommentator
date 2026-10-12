"""Fallback backend: grab the window's client area from the screen with mss.

Sees whatever is on top of the game, so it is only used if Windows Graphics
Capture doesn't work.
"""

import threading
from collections.abc import Callable

import mss
import numpy as np

from commentator.capture.base import LatestFrame
from commentator.core.types import Frame
from commentator.detection import system


class MssBackend:
    name = "mss"

    def __init__(
        self,
        fps: float = 10.0,
        window_state: Callable[[int], system.WindowState] = system.window_state,
    ) -> None:
        self._interval = 1.0 / fps
        self._window_state = window_state
        self._buffer = LatestFrame()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, hwnd: int) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(hwnd,), name="capture-mss", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def latest(self) -> Frame | None:
        return self._buffer.get()

    @property
    def frame_count(self) -> int:
        return self._buffer.count

    def _run(self, hwnd: int) -> None:
        with mss.MSS() as screen:  # mss objects must stay on the thread that made them
            while not self._stop.is_set():
                state = self._window_state(hwnd)
                if not state.exists:
                    return
                if not state.minimized and state.width > 0 and state.height > 0:
                    region = {
                        "left": state.left,
                        "top": state.top,
                        "width": state.width,
                        "height": state.height,
                    }
                    self._buffer.put(Frame(np.array(screen.grab(region))))  # BGRA
                self._stop.wait(self._interval)
