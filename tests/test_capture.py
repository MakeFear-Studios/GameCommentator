import threading

import numpy as np
from fakes import RecordingBus

from commentator.capture.base import LatestFrame
from commentator.capture.manager import CaptureManager
from commentator.core.types import Frame
from commentator.detection.system import WindowState


def frame(value: int = 0) -> Frame:
    return Frame(np.full((4, 6, 4), value, dtype=np.uint8))


# Newest-frame buffer


def test_buffer_is_empty_before_first_frame() -> None:
    assert LatestFrame().get() is None


def test_buffer_keeps_only_newest_frame() -> None:
    buffer = LatestFrame()
    buffer.put(frame(1))
    buffer.put(frame(2))

    latest = buffer.get()

    assert latest is not None and latest.image[0, 0, 0] == 2
    assert buffer.count == 2


def test_buffer_is_thread_safe() -> None:
    buffer = LatestFrame()

    def write() -> None:
        for _ in range(500):
            buffer.put(frame())

    threads = [threading.Thread(target=write) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert buffer.count == 2000


# Manager


class FakeBackend:
    def __init__(self, name: str, works: bool = True, raises: bool = False) -> None:
        self.name = name
        self.works = works
        self.raises = raises
        self.started = self.stopped = False
        self._buffer = LatestFrame()

    def start(self, hwnd: int) -> None:
        if self.raises:
            raise RuntimeError("boom")
        self.started = True
        if self.works:
            self._buffer.put(frame(7))

    def stop(self) -> None:
        self.stopped = True

    def push(self, value: int = 7) -> None:
        self._buffer.put(frame(value))

    def latest(self) -> Frame | None:
        return self._buffer.get()

    @property
    def frame_count(self) -> int:
        return self._buffer.count


FOCUSED = WindowState(exists=True, foreground=True, left=0, top=0, width=6, height=4)


class SavedFrames:
    """Stands in for DevLogger.save_frame."""

    def __init__(self) -> None:
        self.pngs: list[bytes] = []

    def __call__(self, png: bytes) -> str:
        self.pngs.append(png)
        return f"frames/{len(self.pngs):06d}.png"


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_manager(backends, state=FOCUSED, **kwargs):
    event_bus = RecordingBus()
    events = event_bus.events
    current = {"state": state}
    manager = CaptureManager(
        1,
        backends=backends,
        first_frame_timeout=0.1,
        track_interval=60,  # The test drives track() itself.
        window_state=lambda hwnd: current["state"],
        event_bus=event_bus,
        **kwargs,
    )
    return manager, events, current


def test_uses_first_backend_that_delivers_frames() -> None:
    wgc = FakeBackend("wgc")
    manager, events, _ = make_manager([lambda: wgc, lambda: FakeBackend("mss")])

    manager.start()
    try:
        assert manager.backend is wgc
        assert events[0]["type"] == "capture_started"
        assert events[0]["data"] == {"backend": "wgc"}
    finally:
        manager.stop()
    assert wgc.stopped


def test_falls_back_when_backend_sends_no_frames() -> None:
    silent, fallback = FakeBackend("wgc", works=False), FakeBackend("mss")
    manager, events, _ = make_manager([lambda: silent, lambda: fallback])

    manager.start()
    manager.stop()

    assert silent.stopped
    assert [e["type"] for e in events[:2]] == ["backend_failed", "capture_started"]
    assert events[1]["data"]["backend"] == "mss"


def test_falls_back_when_backend_raises() -> None:
    fallback = FakeBackend("mss")
    manager, events, _ = make_manager([lambda: FakeBackend("wgc", raises=True), lambda: fallback])

    manager.start()
    manager.stop()

    assert events[0]["type"] == "backend_failed" and "boom" in events[0]["data"]["error"]


def test_reports_failure_when_no_backend_works() -> None:
    manager, events, _ = make_manager([lambda: FakeBackend("wgc", works=False)])

    manager.start()

    assert manager.backend is None
    assert events[-1]["type"] == "capture_failed"


def test_pauses_when_unfocused_or_minimized_and_resumes() -> None:
    backend = FakeBackend("wgc")
    manager, events, current = make_manager([lambda: backend])
    manager.start()
    try:
        assert manager.latest() is not None

        current["state"] = WindowState(exists=True, foreground=False, width=6, height=4)
        manager.track()
        assert manager.paused and manager.latest() is None

        current["state"] = WindowState(exists=True, minimized=True)
        manager.track()  # Already paused: no second event.

        current["state"] = FOCUSED
        manager.track()
        assert not manager.paused
        assert manager.latest() is None  # The only frame is from before the pause.

        backend.push(9)
        latest = manager.latest()
        assert latest is not None and latest.image[0, 0, 0] == 9
    finally:
        manager.stop()

    pause_events = [e for e in events if e["type"] in ("capture_paused", "capture_resumed")]
    assert [(e["type"], e["data"]) for e in pause_events] == [
        ("capture_paused", {"reason": "unfocused"}),
        ("capture_resumed", {}),
    ]


def test_publishes_stats_and_saves_frames_in_dev_mode() -> None:
    clock = Clock()
    saved = SavedFrames()
    manager, events, _ = make_manager([lambda: FakeBackend("wgc")], save_frame=saved, clock=clock)
    manager.start()
    try:
        clock.now += 5.0
        manager.track()
    finally:
        manager.stop()

    stats = next(e for e in events if e["type"] == "frame_captured")
    assert stats["data"]["frame_size"] == [6, 4]
    assert stats["data"]["backend"] == "wgc"
    assert len(saved.pngs) == 1 and saved.pngs[0][1:4] == b"PNG"
    assert next(e for e in events if e["type"] == "frame_saved")["data"] == {"frame": "frames/000001.png"}


def test_no_stats_or_saved_frames_while_paused() -> None:
    clock = Clock()
    saved = SavedFrames()
    manager, events, current = make_manager([lambda: FakeBackend("wgc")], save_frame=saved, clock=clock)
    manager.start()
    try:
        current["state"] = WindowState(exists=True, minimized=True)
        for _ in range(3):
            clock.now += 5.0
            manager.track()
    finally:
        manager.stop()

    assert not [e for e in events if e["type"] in ("frame_captured", "capture_idle")]
    assert not saved.pngs


def test_resume_restarts_fps_measurement() -> None:
    clock = Clock()
    backend = FakeBackend("wgc")
    manager, events, current = make_manager([lambda: backend], clock=clock)
    manager.start()
    try:
        current["state"] = WindowState(exists=True, minimized=True)
        manager.track()
        clock.now += 60.0  # A long pause shouldn't drag the next fps reading down.
        current["state"] = FOCUSED
        manager.track()
        for _ in range(10):
            backend.push()
        clock.now += 5.0
        manager.track()
    finally:
        manager.stop()

    stats = next(e for e in events if e["type"] == "frame_captured")
    assert stats["data"]["fps"] == 2.0


def test_reports_idle_and_active() -> None:
    clock = Clock()
    backend = FakeBackend("wgc")
    manager, events, _ = make_manager([lambda: backend], clock=clock, stats_interval=1000)
    manager.start()
    try:
        manager.track()  # First frame seen.
        clock.now += 2.5
        manager.track()  # No new frames for 2.5 s.
        manager.track()  # Reported once.
        backend.push()
        manager.track()
    finally:
        manager.stop()

    assert [e["type"] for e in events if e["stage"] == "capture"][1:-1] == [
        "capture_idle",
        "capture_active",
    ]


# Hand-off to the next stage


class RecordingSink:
    def __init__(self, fail: bool = False) -> None:
        self.frames: list[Frame] = []
        self.fail = fail

    def submit(self, frame: Frame) -> None:
        if self.fail:
            raise RuntimeError("vision broke")
        self.frames.append(frame)


def test_sends_each_new_frame_once() -> None:
    backend, sink = FakeBackend("wgc"), RecordingSink()
    manager, _, _ = make_manager([lambda: backend], sink=sink)
    manager.start()
    try:
        manager.track()
        manager.track()  # No new frame: nothing sent.
        backend.push(3)
        manager.track()
    finally:
        manager.stop()

    assert [f.image[0, 0, 0] for f in sink.frames] == [7, 3]


def test_sends_nothing_while_paused_or_stale() -> None:
    backend, sink = FakeBackend("wgc"), RecordingSink()
    manager, _, current = make_manager([lambda: backend], sink=sink)
    manager.start()
    try:
        current["state"] = WindowState(exists=True, minimized=True)
        manager.track()
        backend.push(5)  # Arrives while paused.
        manager.track()
        current["state"] = FOCUSED
        manager.track()  # Resumed, but the newest frame predates the resume.
        backend.push(6)
        manager.track()
    finally:
        manager.stop()

    assert [f.image[0, 0, 0] for f in sink.frames] == [6]


def test_broken_sink_does_not_stop_capture() -> None:
    backend = FakeBackend("wgc")
    manager, events, _ = make_manager([lambda: backend], sink=RecordingSink(fail=True))
    manager.start()
    try:
        manager.track()
        backend.push()
        manager.track()
    finally:
        manager.stop()

    errors = [e for e in events if e["type"] == "sink_error"]
    assert len(errors) == 2 and "vision broke" in errors[0]["data"]["error"]
