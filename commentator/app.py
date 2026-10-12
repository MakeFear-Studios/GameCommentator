"""Startup: DPI awareness, game detection, waiting overlay and capture, wired together.

Temporary home for the wiring until the engine (B5.1) exists.
"""

import argparse
import signal
import sys
import time
from datetime import datetime

from commentator.capture.manager import CaptureManager
from commentator.core import paths
from commentator.core.config import load_game_configs
from commentator.detection import system
from commentator.detection.matcher import Match
from commentator.detection.watcher import GameWatcher
from commentator.events import JsonlLogger, bus


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Game Commentator")
    parser.add_argument("--dev", action="store_true", help="log events and sample frames")
    parser.add_argument("--game", help="force a game config by id, e.g. pico_park")
    parser.add_argument("--window", help="force a window by (part of) its title")
    parser.add_argument("--persona", help="persona config id (not used yet)")
    return parser.parse_args(argv)


def _print_event(record: dict) -> None:
    data = " ".join(f"{k}={v}" for k, v in record["data"].items())
    print(f"[{record['t']:8.2f}] {record['stage']}.{record['type']} {data}", flush=True)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Before any UI or window positions are read.
    dpi_mode = system.enable_dpi_awareness()

    # Imported after DPI awareness so Qt sees the mode we set.
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication

    from commentator.ui.overlay.status import StatusOverlay

    session_dir = paths.logs_dir() / datetime.now().strftime("%Y%m%d-%H%M%S")
    bus.subscribe(_print_event)
    logger = None
    if args.dev:
        logger = JsonlLogger(session_dir / "events.jsonl")
        bus.subscribe(logger)
    bus.publish("app", "session_started", {"dev": args.dev, "dpi": dpi_mode})

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
        frames_dir = session_dir / "frames" if args.dev else None
        capture = CaptureManager(match.window.hwnd, frames_dir=frames_dir)
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
        return 2

    bridge.waiting.connect(lambda: overlay.show_waiting([g.name for g in watcher.games]))
    bridge.found.connect(overlay.show_found)

    # Ctrl+C: Qt's event loop blocks Python signal handling, so wake Python up regularly.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    heartbeat = QTimer()
    heartbeat.start(200)
    heartbeat.timeout.connect(lambda: None)

    watcher.start()
    started = time.monotonic()
    try:
        app.exec()
    finally:
        watcher.stop()
        if capture is not None:
            capture.stop()
        bus.publish("app", "session_ended", {"seconds": round(time.monotonic() - started, 1)})
        if logger is not None:
            logger.close()
    return 0
