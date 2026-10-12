# `commentator/detection`: which game is running, and its window

Task **A2.1 / A2.2** in [`docs/IMPLEMENTATION.md`](../../docs/IMPLEMENTATION.md). Owner: Nathan. Design: [`docs/DESIGN.md` § 4.1](../../docs/DESIGN.md#41-game-detection).

This package finds a configured game among the running processes, hands its window to capture, notices when the game closes, and goes back to waiting. It only **reads** process and window lists: it never opens handles on the game process or sends it input (Easy Anti-Cheat watches for that).

Read this before adding a game, changing how windows are matched, or touching window tracking.

---

## How a game is recognised

Each `config/games/*.yaml` has a `match` block:

```yaml
name: PICO PARK
match:
  process: pico_park.exe      # most reliable; compared case-insensitively
  window_title: "PICO PARK"   # optional; substring, case-insensitive
```

| `match` has | A window matches when |
|---|---|
| `process` only | it belongs to a process with that exe name |
| `process` + `window_title` | it belongs to that process; the title only **narrows** the choice if several windows match it (falls back to all the process's windows) |
| `window_title` only | its title contains the text, whatever the process |
| nothing (e.g. `generic.yaml`) | never matched automatically |

- Configs are tried in file-name order; the **first** with a matching window wins.
- Of the matching windows, the **largest** is the game (launchers and splash windows are smaller).
- A matching process **without a window yet** (still launching) is not a match; the next poll catches the window.
- Only visible, titled, top-level, non-tool windows are considered (`system.list_windows`).

**Adding a game needs config only.** Find the exe name with `tasklist | findstr /i <name>` while the game runs.

---

## The loop (`watcher.py`)

```
waiting ──(match found)──► found ──(window gone)──► lost ──► waiting
   ▲  poll every 2 s           │ IsWindow(hwnd) every 2 s
   └───────────────────────────┘
```

```python
watcher = GameWatcher(
    games,                       # list[GameConfig]
    on_waiting=...,              # () -> None, once per waiting spell
    on_found=...,                # (Match) -> None: start capture here
    on_lost=...,                 # (Match) -> None: stop capture here
    forced_game="pico_park",     # --game: only this config
    forced_window="Notepad",     # --window: match any window by title
)
watcher.start()  ...  watcher.stop()
```

- Callbacks run on the **detection thread**. The app forwards them to Qt through signals (`commentator/app.py`).
- `poll()` is one step of the loop and is public so tests drive it without threads.
- An exception inside a poll is published as `detection / error` and the loop carries on.
- `--game <id>` restricts matching to that config; an unknown id raises `ValueError` listing the known ids.
- `--window "<title>"` matches by title, using `--game`'s config if given, otherwise a `generic` placeholder.

## Matching (`matcher.py`)

Pure logic, no Windows calls:

```python
find_game(processes: Sequence[ProcessInfo], windows: Sequence[WindowInfo], games) -> Match | None
```

`ProcessInfo(pid, name)`, `WindowInfo(hwnd, pid, title, width, height)`, `Match(game, window)`.

## Windows calls (`system.py`)

| Function | What it does |
|---|---|
| `enable_dpi_awareness()` | Per-monitor v2 DPI awareness, falling back to older APIs. **Call first, before Qt or any window position.** Returns the mode it got. |
| `list_processes()` | `psutil` pid + exe name |
| `list_windows()` | Visible, titled, top-level, non-tool windows with their pid and size |
| `is_window(hwnd)` | Still exists? |
| `window_state(hwnd)` | `WindowState(exists, minimized, foreground, left, top, width, height)`: the **client area** in physical screen pixels |

---

## Published events (`stage="detection"`)

| Type | Data | When |
|---|---|---|
| `waiting` | `games` (names) | Once each time it starts waiting |
| `game_found` | `game`, `hwnd`, `pid`, `title` | A configured game's window was found |
| `game_lost` | `game` | Its window closed |
| `error` | `error` | A poll raised; the loop continues |

The app also publishes `app / dpi_awareness_set` with the mode.

---

## Testing

```bash
python -m pytest tests/test_detection.py -q
```

Covered with fake process and window lists (no game needed): config loading; match by exe, case-insensitively; no match when not running; process without a window yet; largest window wins; title narrowing; other processes' windows ignored; title-only configs; config order; the watcher's waiting → found → lost → waiting cycle with events and callbacks (waiting announced once); already-running games; configs without `match` skipped; `--game` and unknown ids; `--window`.

Not unit-tested (Windows calls): `system.py`. Checked by hand with PICO PARK.

---

## Status and next steps

- ✅ Detection, waiting/found/lost loop, DPI awareness, window state. Works end to end with PICO PARK.
- ⬜ Add `config/games/elden_ring.yaml` (`process: eldenring.exe`) and check it with the real game; its anti-cheat launcher (`start_protected_game.exe`) runs briefly first and must not be matched.
- ⬜ "Pick a window" from the tray (B5.3) for games without a config, using `generic.yaml`.
- Possible later: event-driven window tracking (`SetWinEventHook`) instead of polling.
