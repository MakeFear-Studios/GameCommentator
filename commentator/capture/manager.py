"""Run capture for one game window: pick a backend, track the window, pause when unfocused."""

import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import cv2

from commentator.capture.base import CaptureBackend, Frame
from commentator.detection import system
from commentator.events import EventBus, bus

BackendFactory = Callable[[], CaptureBackend]


def default_backends() -> list[BackendFactory]:
    from commentator.capture.mss_backend import MssBackend
    from commentator.capture.wgc import WgcBackend

    return [WgcBackend, MssBackend]


class CaptureManager:
    """Starts the first backend that delivers a frame, then watches the window.

    `latest()` returns None while paused (game minimised or not focused) and after a
    resume until a new frame arrives, so later stages never see a stale picture.
    """

    def __init__(
        self,
        hwnd: int,
        *,
        backends: Sequence[BackendFactory] | None = None,
        first_frame_timeout: float = 2.0,
        track_interval: float = 0.25,
        stats_interval: float = 5.0,
        idle_after: float = 2.0,
        frames_dir: Path | None = None,
        save_interval: float = 5.0,
        window_state: Callable[[int], system.WindowState] = system.window_state,
        event_bus: EventBus = bus,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hwnd = hwnd
        self._factories = list(backends) if backends is not None else default_backends()
        self._first_frame_timeout = first_frame_timeout
        self._track_interval = track_interval
        self._stats_interval = stats_interval
        self._idle_after = idle_after
        self._frames_dir = frames_dir
        self._save_interval = save_interval
        self._window_state = window_state
        self._bus = event_bus
        self._clock = clock
        self.backend: CaptureBackend | None = None
        self.paused = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_stats = 0.0
        self._last_stats_count = 0
        self._last_save = 0.0
        # Frames up to this count were captured before the last pause; don't hand them out.
        self._stale_count = 0
        self._last_count = 0
        self._last_new_frame = 0.0
        self.idle = False

    # Backend selection

    def start(self) -> None:
        self.backend = self._start_backend()
        if self.backend is None:
            self._bus.publish("capture", "capture_failed", {"hwnd": self.hwnd})
            return
        self._last_stats = self._last_save = self._last_new_frame = self._clock()
        self._thread = threading.Thread(target=self._run, name="capture-track", daemon=True)
        self._thread.start()

    def _start_backend(self) -> CaptureBackend | None:
        for factory in self._factories:
            backend = factory()
            try:
                backend.start(self.hwnd)
            except Exception as error:
                self._bus.publish(
                    "capture", "backend_failed", {"backend": backend.name, "error": repr(error)}
                )
                continue
            if self._wait_for_first_frame(backend):
                self._bus.publish("capture", "capture_started", {"backend": backend.name})
                return backend
            backend.stop()
            self._bus.publish(
                "capture", "backend_failed", {"backend": backend.name, "error": "no frames"}
            )
        return None

    def _wait_for_first_frame(self, backend: CaptureBackend) -> bool:
        deadline = time.monotonic() + self._first_frame_timeout
        while time.monotonic() < deadline:
            if backend.latest() is not None:
                return True
            # A minimised window sends no frames; don't blame the backend for that.
            if self._window_state(self.hwnd).minimized:
                return True
            time.sleep(0.05)
        return backend.latest() is not None

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self.backend:
            self.backend.stop()
            self._bus.publish("capture", "capture_stopped", {"backend": self.backend.name})
            self.backend = None

    def latest(self) -> Frame | None:
        if self.paused or self.backend is None:
            return None
        if self.backend.frame_count <= self._stale_count:
            return None  # Resumed, but no frame since then yet.
        return self.backend.latest()

    # Window tracking

    def _run(self) -> None:
        while not self._stop.is_set():
            self.track()
            self._stop.wait(self._track_interval)

    def track(self) -> None:
        """One tracking step. Public so tests can drive it without threads."""
        assert self.backend is not None
        state = self._window_state(self.hwnd)
        if not state.exists:
            return  # Detection notices and stops us.
        now = self._clock()
        count = self.backend.frame_count
        should_pause = state.minimized or not state.foreground
        if should_pause != self.paused:
            self.paused = should_pause
            if should_pause:
                reason = "minimized" if state.minimized else "unfocused"
                self._bus.publish("capture", "capture_paused", {"reason": reason})
            else:
                # Everything captured so far predates the resume.
                self._stale_count = count
                self._last_stats, self._last_stats_count = now, count
                self._last_new_frame = now
                self._bus.publish("capture", "capture_resumed", {})
        if self.paused:
            return

        self._check_idle(count, now)
        if now - self._last_stats >= self._stats_interval:
            self._publish_stats(state, now)
        if self._frames_dir and now - self._last_save >= self._save_interval:
            self._save_frame(now)

    def _check_idle(self, count: int, now: float) -> None:
        """Report when frames stop arriving while the game is focused.

        Windows Graphics Capture only sends frames when the picture changes, so this can
        also mean a static screen, not only a stall.
        """
        if count != self._last_count:
            self._last_count, self._last_new_frame = count, now
            if self.idle:
                self.idle = False
                self._bus.publish("capture", "capture_active", {})
        elif not self.idle and now - self._last_new_frame >= self._idle_after:
            self.idle = True
            self._bus.publish(
                "capture", "capture_idle", {"seconds": round(now - self._last_new_frame, 1)}
            )

    def _publish_stats(self, state: system.WindowState, now: float) -> None:
        assert self.backend is not None
        count = self.backend.frame_count
        fps = (count - self._last_stats_count) / (now - self._last_stats)
        frame = self.backend.latest()
        self._bus.publish(
            "capture",
            "frame_captured",
            {
                "backend": self.backend.name,
                "fps": round(fps, 1),
                "frames": count,
                "frame_size": [frame.width, frame.height] if frame else None,
                "window": [state.left, state.top, state.width, state.height],
                "paused": self.paused,
            },
        )
        self._last_stats, self._last_stats_count = now, count

    def _save_frame(self, now: float) -> None:
        frame = self.latest()
        if frame is None:
            return
        assert self._frames_dir is not None
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        path = self._frames_dir / f"frame_{now:010.1f}.png"
        cv2.imwrite(str(path), frame.image)
        self._last_save = now
        self._bus.publish("capture", "frame_saved", {"path": str(path)})
