"""Match running processes and windows against game configs.

Pure logic with no Windows calls, so it is tested with fake lists.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from commentator.core.config import GameConfig, GameMatch


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str


@dataclass(frozen=True)
class WindowInfo:
    """A visible, titled, top-level window (system.list_windows filters the rest out)."""

    hwnd: int
    pid: int
    title: str
    width: int
    height: int

    @property
    def area(self) -> int:
        return max(self.width, 0) * max(self.height, 0)


@dataclass(frozen=True)
class Match:
    game: GameConfig
    window: WindowInfo


def _title_matches(title: str, wanted: str) -> bool:
    return wanted.casefold() in title.casefold()


def _candidate_windows(
    match: GameMatch, processes: Sequence[ProcessInfo], windows: Sequence[WindowInfo]
) -> list[WindowInfo]:
    if match.process:
        wanted = match.process.casefold()
        pids = {p.pid for p in processes if p.name and p.name.casefold() == wanted}
        candidates = [w for w in windows if w.pid in pids]
        # The process is the reliable check; the title only narrows it down when it helps.
        if match.window_title:
            titled = [w for w in candidates if _title_matches(w.title, match.window_title)]
            candidates = titled or candidates
        return candidates
    if match.window_title:
        return [w for w in windows if _title_matches(w.title, match.window_title)]
    return []


def find_game(
    processes: Sequence[ProcessInfo],
    windows: Sequence[WindowInfo],
    games: Iterable[GameConfig],
) -> Match | None:
    """Return the first game (in config order) with a matching window, using its largest window.

    A matching process without a window yet (still launching) is not a match.
    """
    for game in games:
        candidates = _candidate_windows(game.match, processes, windows)
        if candidates:
            return Match(game=game, window=max(candidates, key=lambda w: w.area))
    return None
