"""Background loop: wait for a known game, attach, notice when it closes, wait again."""

import threading
from collections.abc import Callable, Sequence

from commentator.core.config import GameConfig, GameMatch
from commentator.detection import system
from commentator.detection.matcher import Match, ProcessInfo, WindowInfo, find_game
from commentator.logs import EventBus, bus


class GameWatcher:
    """Polls for a game every `poll_interval` seconds.

    States: waiting -> found -> (window gone) -> lost -> waiting.
    Callbacks run on the watcher thread.
    """

    def __init__(
        self,
        games: Sequence[GameConfig],
        on_waiting: Callable[[], None] = lambda: None,
        on_found: Callable[[Match], None] = lambda m: None,
        on_lost: Callable[[Match], None] = lambda m: None,
        *,
        poll_interval: float = 2.0,
        forced_game: str | None = None,
        forced_window: str | None = None,
        list_processes: Callable[[], Sequence[ProcessInfo]] = system.list_processes,
        list_windows: Callable[[], Sequence[WindowInfo]] = system.list_windows,
        is_window: Callable[[int], bool] = system.is_window,
        event_bus: EventBus = bus,
    ) -> None:
        self.games = _select_games(games, forced_game, forced_window)
        self.on_waiting = on_waiting
        self.on_found = on_found
        self.on_lost = on_lost
        self.poll_interval = poll_interval
        self._list_processes = list_processes
        self._list_windows = list_windows
        self._is_window = is_window
        self._bus = event_bus
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.current: Match | None = None
        self._announced_waiting = False

    def poll(self) -> None:
        """One step of the loop. Public so tests can drive it without threads."""
        if self.current is None:
            match = find_game(self._list_processes(), self._list_windows(), self.games)
            if match is None:
                if not self._announced_waiting:
                    self._announced_waiting = True
                    self._bus.publish("detection", "waiting", {"games": [g.name for g in self.games]})
                    self.on_waiting()
                return
            self.current = match
            self._announced_waiting = False
            self._bus.publish(
                "detection",
                "game_found",
                {
                    "game": match.game.id,
                    "hwnd": match.window.hwnd,
                    "pid": match.window.pid,
                    "title": match.window.title,
                },
            )
            self.on_found(match)
        elif not self._is_window(self.current.window.hwnd):
            lost, self.current = self.current, None
            self._bus.publish("detection", "game_lost", {"game": lost.game.id})
            self.on_lost(lost)
            self.poll()  # Announce waiting (or a new match) straight away.

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="detection", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as error:  # Keep watching; a bad poll shouldn't kill detection.
                self._bus.publish("detection", "error", {"error": repr(error)})
            self._stop.wait(self.poll_interval)


def _select_games(games: Sequence[GameConfig], forced_game: str | None, forced_window: str | None) -> list[GameConfig]:
    """Apply the --game / --window overrides."""
    selected = [g for g in games if not g.match.is_empty]
    if forced_game:
        selected = [g for g in games if g.id == forced_game]
        if not selected:
            known = ", ".join(g.id for g in games) or "none"
            raise ValueError(f"Unknown game '{forced_game}'. Known games: {known}")
    if forced_window:
        base = selected[0] if forced_game else GameConfig("generic", forced_window, GameMatch())
        selected = [GameConfig(base.id, base.name, GameMatch(window_title=forced_window))]
    return selected
