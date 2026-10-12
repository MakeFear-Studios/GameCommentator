"""Startup: DPI awareness, game detection, waiting overlay and capture, wired together.

Temporary home for the wiring until the engine (B5.1) exists.
"""

import argparse
import signal
import sys

from commentator.capture.manager import CaptureManager
from commentator.core.config import load_game_configs
from commentator.core.types import NullFrameSink
from commentator.detection import system
from commentator.detection.matcher import Match
from commentator.detection.watcher import GameWatcher
from commentator.logs import DevLogger, LogEvent, bus


def _print_event(event: LogEvent) -> None:
    data = " ".join(f"{k}={v}" for k, v in event.data.items())
    print(f"[{event.t:8.2f}] {event.stage}.{event.type} {data}", flush=True)


def run(args: argparse.Namespace, dev_logger: DevLogger | None) -> int:
    # Before any UI or window positions are read.
    dpi_mode = system.enable_dpi_awareness()

    # Imported after DPI awareness so Qt sees the mode we set.
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication

    from commentator.ui.overlay.status import StatusOverlay

    bus.subscribe(_print_event)
    bus.publish("app", "dpi_awareness_set", {"mode": dpi_mode})

    games = load_game_configs()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    overlay = StatusOverlay()

    class Bridge(QObject):
        """Moves watcher callbacks (detection thread) onto the Qt thread."""

        waiting = Signal()
        found = Signal(str, object)

    bridge = Bridge()
    capture: CaptureManager | None = None

    def on_found(match: Match) -> None:
        nonlocal capture
        capture = CaptureManager(
            match.window.hwnd,
            sink=NullFrameSink(),  # The vision stage (A3) replaces this.
            save_frame=dev_logger.save_frame if dev_logger else None,
        )
        capture.start()
        state = system.window_state(match.window.hwnd)
        rect = (state.left, state.top, state.width, state.height) if state.exists else None
        bridge.found.emit(match.game.name, rect)

    def on_lost(match: Match) -> None:
        nonlocal capture
        if capture is not None:
            capture.stop()
            capture = None

    try:
        watcher = GameWatcher(
            games,
            on_waiting=bridge.waiting.emit,
            on_found=on_found,
            on_lost=on_lost,
            forced_game=args.game,
            forced_window=args.window,
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        bus.unsubscribe(_print_event)
        return 2

    bridge.waiting.connect(lambda: overlay.show_waiting([g.name for g in watcher.games]))
    bridge.found.connect(overlay.show_found)

    # Ctrl+C: Qt's event loop blocks Python signal handling, so wake Python up regularly.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.start(200)
    heartbeat.timeout.connect(lambda: None)

    watcher.start()
    try:
        app.exec()
    finally:
        watcher.stop()
        if capture is not None:
            capture.stop()
        bus.flush()
        bus.unsubscribe(_print_event)
    return 0
