# CLAUDE.md

Shared context for Claude sessions working in this repo. Read by every collaborator's Claude, so keep it accurate and public-safe. Personal preferences go in `CLAUDE.local.md` (git-ignored).

## Project

**Game Commentator**: a real-time AI commentator for your own gameplay. It watches the game window, works out what's happening, and narrates it out loud. First target: **Elden Ring boss fights**. A Windows companion app (never a mod), local-first with swappable models, built as a portfolio project by **Welsby** and **Nathan**. The repo is owned by the **MakeFear-Studios** GitHub organization: `git@github.com:MakeFear-Studios/Game-Commentor.git`.

**Status:** startup, game detection, the waiting overlay and window capture (task A2) work, tested with PICO PARK. Everything else is still planned; the folder layout in the README is the plan.

## Read the right doc

| Need | Read |
|---|---|
| Overview, features, structure | `README.md` |
| How it works and why (stages, configs, packaging, testing) | `docs/DESIGN.md` |
| Who owns what, contracts, task IDs, milestones, status, decision log | `docs/IMPLEMENTATION.md` |
| How a built package works (API, guarantees, formats, tests, next steps) | `commentator/<package>/README.md`, e.g. `commentator/logs/README.md` |

Before starting a task, find its ID in `docs/IMPLEMENTATION.md` and read the **Contracts** section plus that task's track. Before using or changing an existing package, read its `README.md`.

When you finish implementing a package (or change one), write or update its `README.md` so the next person or agent can work from it without reading all the code.

## Pipeline

Detect game (or show a "waiting for game" overlay until one starts) → window capture (newest frame) → **vision LLM** (frame + game config + previous state → state + events with priority, structured JSON) → validation (schema, known events, TTL, cooldown de-dup) → **director** (priority stack, expiry, interrupts) → **narrator LLM** (event + memory → one line) → **TTS** → chunked, stoppable playback. Every stage publishes to the log bus (`commentator/logs/`, `from commentator.logs import bus`); `--dev` logs to `logs/<session>/events.jsonl` and feeds a dev dashboard. `commentator/events/` is reserved and TBD; don't put logging there.

## Key decisions (don't silently undo these)

- **Models see and judge; code decides when to speak.** The vision LLM picks events and priorities from the game config's catalogue; code validates them and owns all timing.
- **Director uses a priority stack:** one newest-first (LIFO) stack per priority level (`critical` > `high` > `normal` > `filler`); take the top of the highest non-empty stack; the rest of that stack goes to the narrator as context; stacks are capped; expired events are dropped on read; critical events interrupt.
- **Memory is in RAM only:** fight memory + a capped session summary. No database.
- **Modular:** game (`config/games/`), persona (`config/personas/`) and backends (`config/backends.yaml`) are independent YAML. New games, styles or models should need config, not code.
- **Engine separate from front ends:** CLI, tray, overlay and dev dashboard only call engine controls.
- **Read-only:** never send inputs to the game. Hotkeys only listen.
- **Distribution:** PyInstaller one-folder app inside an Inno Setup installer that installs Ollama if missing. Models stay in Ollama, not in the app.

If a change conflicts with one of these, raise it rather than working around it, and record any agreed change in the decision log.

## Ownership

- **Welsby (Track A, "see and ship"):** `vision/`, `logs/`, `ui/dev/`, `config/games/`, `packaging/`, `.github/`, the OpenAI-compatible / Ollama backend.
- **Nathan (Track B, "think and speak"):** `detection/`, `capture/` (startup and screenshots, task A2), `director/`, `narrator/`, `voice/`, `memory/`, `engine/`, `ui/` (cli, tray, overlay), `config/personas/`, TTS and API backends.
- **Shared:** `commentator/core/` (types, config loading, paths) is the contract between tracks. Changes need a PR labelled `contract` and both approvals.

Stay inside the owner's folders for the task at hand. Track B builds against `FakeEventSource` / `ReplayEventSource`, so it never needs the game running.

## Conventions

- Python 3.13, Windows. Use the repo's virtual environment: `py -3.13 -m venv .venv`, then `.venv\Scripts\Activate.ps1` (or call `.venv\Scripts\python.exe` directly), then `pip install -e . pytest ruff`. Run with `python main.py` (`--dev`, `--game`, `--window`, `--persona`); test with `python -m pytest tests`.
- Type hints; ruff for lint and format; pytest for tests. Director logic is tested with a fake clock.
- Branches: `welsby/<topic>` or `nathan/<topic>`, one per task ID. `main` is protected; PRs need a review from the other person; squash merge.
- PR titles start with the task ID, e.g. `B1.2: director priority stack`. Keep PRs small.
- Commit messages: short imperative subject line.
- Definition of done: tests pass, ruff clean, the stage publishes its key decisions to the event bus, and the task's status in `docs/IMPLEMENTATION.md` is updated.
- When a design decision changes, update `docs/DESIGN.md`, `docs/IMPLEMENTATION.md`, the README and this file together.

## Gotchas

- Play the game in **borderless windowed** mode; capture can fail in exclusive fullscreen.
- Make the process DPI-aware before reading window positions.
- Local models run in **Ollama** (`http://localhost:11434`, OpenAI-compatible at `/v1`). Use structured output (`format` / JSON schema) and `keep_alive` so models stay loaded.
- The GPU is shared with the game: default to small (3–4B) models.
- `logs/` and `CLAUDE.local.md` are git-ignored; never commit API keys.
- **The app ships as a PyInstaller .exe.** Never write next to the code or the .exe: Program Files isn't writable. Use `%LOCALAPPDATA%\GameCommentator\` when `sys.frozen` is set (see `default_log_root()` in `commentator/logs/session.py`). The windowed .exe has no console: `sys.stdout`/`sys.stderr` can be `None`, so don't rely on `print` for anything important; publish to the log bus instead.
- Dev mode: `python main.py --dev`, or `GAME_COMMENTATOR_DEV=1` for the .exe. Logs go to `<repo>/logs/` from source.
