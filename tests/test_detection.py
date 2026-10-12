from pathlib import Path

import pytest

from commentator.core.config import GameConfig, GameMatch, load_game_configs
from commentator.detection.matcher import ProcessInfo, WindowInfo, find_game
from commentator.detection.watcher import GameWatcher
from commentator.events import EventBus

ELDEN = GameConfig("elden_ring", "ELDEN RING", GameMatch("eldenring.exe", "ELDEN RING"))
PICO = GameConfig("pico_park", "PICO PARK", GameMatch(process="pico_park.exe"))
TITLE_ONLY = GameConfig("titled", "Titled", GameMatch(window_title="My Game"))


def proc(pid: int, name: str) -> ProcessInfo:
    return ProcessInfo(pid, name)


def win(hwnd: int, pid: int, title: str, w: int = 1920, h: int = 1080) -> WindowInfo:
    return WindowInfo(hwnd, pid, title, w, h)


# Config loading


def test_loads_game_configs(tmp_path: Path) -> None:
    (tmp_path / "pico_park.yaml").write_text(
        "name: PICO PARK\nmatch:\n  process: pico_park.exe\n", encoding="utf-8"
    )
    (tmp_path / "generic.yaml").write_text("name: Any game\n", encoding="utf-8")

    games = load_game_configs(tmp_path)

    assert [g.id for g in games] == ["generic", "pico_park"]
    assert games[1].match == GameMatch(process="pico_park.exe")
    assert games[0].match.is_empty


def test_repo_configs_load() -> None:
    assert "pico_park" in {g.id for g in load_game_configs()}


# Matcher


def test_matches_by_process_name_case_insensitively() -> None:
    match = find_game([proc(10, "PICO_PARK.EXE")], [win(1, 10, "PICO PARK")], [PICO])

    assert match is not None
    assert match.game is PICO
    assert match.window.hwnd == 1


def test_no_match_when_game_not_running() -> None:
    assert find_game([proc(10, "notepad.exe")], [win(1, 10, "Untitled - Notepad")], [PICO]) is None


def test_process_without_window_is_not_a_match_yet() -> None:
    assert find_game([proc(10, "pico_park.exe")], [], [PICO]) is None


def test_picks_largest_window_of_the_process() -> None:
    windows = [win(1, 10, "Launcher", 400, 300), win(2, 10, "PICO PARK", 1920, 1080)]

    match = find_game([proc(10, "pico_park.exe")], windows, [PICO])

    assert match is not None and match.window.hwnd == 2


def test_title_narrows_process_windows() -> None:
    windows = [win(1, 10, "Big splash", 2560, 1440), win(2, 10, "ELDEN RING™", 1920, 1080)]

    match = find_game([proc(10, "eldenring.exe")], windows, [ELDEN])

    assert match is not None and match.window.hwnd == 2


def test_ignores_windows_of_other_processes() -> None:
    windows = [win(1, 99, "PICO PARK fan wiki - Browser"), win(2, 10, "PICO PARK", 800, 600)]

    match = find_game([proc(10, "pico_park.exe"), proc(99, "chrome.exe")], windows, [PICO])

    assert match is not None and match.window.hwnd == 2


def test_title_only_config_matches_any_process() -> None:
    match = find_game([proc(5, "game.exe")], [win(3, 5, "My Game v1.2")], [TITLE_ONLY])

    assert match is not None and match.window.hwnd == 3


def test_first_config_wins() -> None:
    processes = [proc(10, "pico_park.exe"), proc(20, "eldenring.exe")]
    windows = [win(1, 10, "PICO PARK"), win(2, 20, "ELDEN RING")]

    match = find_game(processes, windows, [ELDEN, PICO])

    assert match is not None and match.game is ELDEN


# Watcher


class FakeSystem:
    def __init__(self) -> None:
        self.processes: list[ProcessInfo] = []
        self.windows: list[WindowInfo] = []

    def start_game(self) -> None:
        self.processes = [proc(10, "pico_park.exe")]
        self.windows = [win(1, 10, "PICO PARK")]

    def close_game(self) -> None:
        self.processes, self.windows = [], []

    def is_window(self, hwnd: int) -> bool:
        return any(w.hwnd == hwnd for w in self.windows)


def make_watcher(fake: FakeSystem, calls: list[str], **kwargs) -> tuple[GameWatcher, list[dict]]:
    events: list[dict] = []
    event_bus = EventBus()
    event_bus.subscribe(events.append)
    watcher = GameWatcher(
        kwargs.pop("games", [PICO]),
        on_waiting=lambda: calls.append("waiting"),
        on_found=lambda m: calls.append(f"found:{m.game.id}"),
        on_lost=lambda m: calls.append(f"lost:{m.game.id}"),
        list_processes=lambda: fake.processes,
        list_windows=lambda: fake.windows,
        is_window=fake.is_window,
        event_bus=event_bus,
        **kwargs,
    )
    return watcher, events


def test_watcher_waits_then_attaches_then_returns_to_waiting() -> None:
    fake, calls = FakeSystem(), []
    watcher, events = make_watcher(fake, calls)

    watcher.poll()
    watcher.poll()  # Still waiting: announced only once.
    fake.start_game()
    watcher.poll()
    watcher.poll()  # Still attached.
    fake.close_game()
    watcher.poll()

    assert calls == ["waiting", "found:pico_park", "lost:pico_park", "waiting"]
    assert [e["type"] for e in events] == ["waiting", "game_found", "game_lost", "waiting"]
    assert events[1]["data"]["hwnd"] == 1


def test_watcher_attaches_immediately_if_game_already_running() -> None:
    fake, calls = FakeSystem(), []
    fake.start_game()
    watcher, _ = make_watcher(fake, calls)

    watcher.poll()

    assert calls == ["found:pico_park"]


def test_watcher_skips_configs_without_match() -> None:
    generic = GameConfig("generic", "Any game", GameMatch())
    watcher, _ = make_watcher(FakeSystem(), [], games=[generic, PICO])

    assert watcher.games == [PICO]


def test_forced_game_limits_matching() -> None:
    fake, calls = FakeSystem(), []
    fake.start_game()
    watcher, _ = make_watcher(fake, calls, games=[PICO, ELDEN], forced_game="elden_ring")

    watcher.poll()

    assert calls == ["waiting"]


def test_unknown_forced_game_is_an_error() -> None:
    with pytest.raises(ValueError, match="Unknown game 'nope'"):
        make_watcher(FakeSystem(), [], forced_game="nope")


def test_forced_window_matches_by_title() -> None:
    fake, calls = FakeSystem(), []
    fake.windows = [win(7, 3, "Some Other Game")]
    watcher, _ = make_watcher(fake, calls, forced_window="other game")

    watcher.poll()

    assert calls == ["found:generic"]
