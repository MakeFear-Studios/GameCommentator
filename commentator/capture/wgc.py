"""Default backend: Windows Graphics Capture of one window, wherever it is."""

from windows_capture import CaptureControl, InternalCaptureControl, WindowsCapture
from windows_capture import Frame as WgcFrame

from commentator.capture.base import LatestFrame
from commentator.core.types import Frame


class WgcBackend:
    name = "windows-capture"

    def __init__(self, max_fps: float = 10.0) -> None:
        # Vision reads about one frame a second; copying every frame at 60 fps wastes CPU.
        self._min_interval_ms = int(1000 / max_fps)
        self._buffer = LatestFrame()
        self._control: CaptureControl | None = None
        self.closed = False

    def start(self, hwnd: int) -> None:
        options = {
            "cursor_capture": False,
            "minimum_update_interval": self._min_interval_ms,
            "window_hwnd": hwnd,
        }
        try:
            capture = WindowsCapture(draw_border=False, **options)
        except Exception:
            # Hiding the yellow border isn't supported on every Windows build.
            capture = WindowsCapture(draw_border=None, **options)

        # windows-capture picks handlers by function name.
        @capture.event
        def on_frame_arrived(frame: WgcFrame, control: InternalCaptureControl) -> None:
            # The buffer is a view into native memory that is reused, so copy it.
            self._buffer.put(Frame(frame.frame_buffer.copy()))

        @capture.event
        def on_closed() -> None:
            self.closed = True

        self._control = capture.start_free_threaded()

    def stop(self) -> None:
        if self._control is not None:
            self._control.stop()
            self._control = None

    def latest(self) -> Frame | None:
        return self._buffer.get()

    @property
    def frame_count(self) -> int:
        return self._buffer.count
