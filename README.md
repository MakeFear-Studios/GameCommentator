# Game Commentator

A real-time AI commentator for your own gameplay. It watches your game, works out what's happening, and narrates it out loud, like a sports caster calling a title fight. It reacts instantly to big moments and stays quiet when nothing is going on.

The first target is **Elden Ring boss fights**.

> **Status:** early setup. Only `main.py` exists so far; the structure below is the planned layout.

---

## What it does

- Detects which game you're playing and finds its window automatically, on any monitor
- Captures that window while you play
- Reads the game's HUD (your HP, boss HP, "YOU DIED", "ENEMY FELLED") with fast pixel checks
- Uses a vision model to describe what's happening ("boss leaping into the air", "player summoned spirit ashes")
- Turns changes between frames into **events** such as `big_hit_landed`, `low_hp`, `you_died`, `boss_felled`
- Decides **when** to speak and **what** to talk about using a priority queue:
  - `filler` / `normal` lines are dropped if something is already playing
  - `high` events are queued to play next
  - `critical` events (death, boss felled) **interrupt** whatever is being said
  - every line has an expiry time, so stale reactions are never spoken
- Writes a short commentary line with an LLM and speaks it with text-to-speech
- Remembers the current fight (deaths, the move that keeps hitting you, your weapon and the boss's) and the key moments of your play session (progress, closest calls, how you're doing), so lines build on each other: *"Attempt 23 on Malenia… boss at 18%, the furthest he's ever got!"*

## Outcome

- **Live, spoken commentary** on Elden Ring boss fights that feels responsive, not robotic
- **Runs fully local and free** on a consumer GPU alongside the game (developed on an RTX 4070 Ti); no API keys required
- **Modular by design**: three independent parts can be swapped without touching the engine:
  - **Game config**: what to track and what counts as an event (`config/games/*.yaml`)
  - **Persona**: how it talks: style, example lines, voice, chattiness (`config/personas/*.yaml`)
  - **Model backends**: local (Ollama, Kokoro, Piper) or API (OpenAI-compatible, Claude, Gemini, ElevenLabs)
- **Download and run**: a Windows app (.exe) that runs alongside the game, not a mod
- **Read-only**: it only looks at the game window and never sends inputs to the game

## How it works

![Architecture diagram](docs/architecture.png)

```
Detect game ──► Window capture ──► OpenCV checks (fast, ~100ms) ──┐
                               └─► Vision model (~1s) ────────────┴─► Compare + rules ──► Director ──► Narrator LLM ──► TTS ──► Speaker
                                                                      (state → events)   (priority,    (event → line)  (voice)  (stoppable)
                                                                                          expiry,
                                                                                          interrupts)
```

| Stage | Type | Job |
|---|---|---|
| Detection | Code | Work out which game is running and find its window |
| Window capture | Code | Grab frames from the game window |
| OpenCV checks | Code | Read HP bars, boss bar, death and victory screens |
| Vision model (VLM) | Model | Describe the scene and anything unusual |
| Compare + rules | Code | Compare the current state with the last one and emit events with priority and expiry |
| Director | Code | Pick which event to narrate and when; handle queueing and interrupts |
| Narrator | Model | Write one short line in the persona's style |
| TTS | Model | Turn the line into speech |
| Speaker | Code | Play audio in chunks so it can be cut off for critical events |

**Design principle:** *models for perception and language, code for decisions.*

## Memory

Two kinds of memory make the commentary smarter than one-off reactions. Both live in memory while the app runs and are passed to the narrator as context; nothing is saved to disk.

**Fight memory (short-term):** live, reset for each boss fight.
- Who you're fighting, attempt number, deaths this session
- **What hit you:** the vision model describes the attack whenever you take a big hit, so it can call out repeats (*"The delayed slam. AGAIN."*)
- **Weapons:** yours (from the equipment icons) and the boss's (from the scene)
- Streaks and patterns, flasks, summons, phase, the boss's lowest HP this fight

**Session memory (longer-term):** a small rolling summary kept for the whole play session.
- **Progress this session:** bosses beaten and current region (read from the area-name banner)
- **Key moments:** closest calls, best attempts, the boss giving you the most trouble
- **Skill read:** attempts per boss, hits taken, whether you're improving or struggling
- **Habits:** the attack types that keep catching you, weapons used

It's kept small on purpose: a few counters plus a short list of important facts, with older or less important ones dropping off so it always fits in the narrator's context window. When a fight ends, fight memory is boiled down to a few lines and added here.

```
events ──► Fight memory (live) ──► summarised at fight end ──► Session memory (rolling summary)
                │                                                  │
                └──────────► Context builder ◄─────────────────────┘
                                   │  short, pre-summarised facts
                                   ▼
                              Narrator LLM
```

Code calculates the stats and a **context builder** turns them into a few short facts for the narrator, which keeps prompts small enough for local models. Each persona uses the same memory in its own way: the sports caster hypes the comeback, the dark narrator turns it into lore.

## How it runs

Game Commentator is a **Windows companion app** that runs alongside the game. It is not a mod: nothing is injected into the game, so it works with anti-cheat and isn't tied to one game.

- **Finds the game automatically.** Each game config says how to recognise its game (process name, window title). The app checks what's running, loads the matching config, and falls back to a generic vision-only mode for any other game. You can always pick the game or window manually.
- **Follows the game window, not the screen.** It captures only the game's window, so it works on any monitor, keeps up if the window moves, and pauses when you alt-tab. HUD positions are stored as fractions of the window, so any resolution works.
- **One engine, several front ends.** The engine exposes simple controls (start, stop, mute, switch persona), and each way of running it just calls those:
  - **Command line**: `python main.py`, for development
  - **Tray app**: runs in the background with global hotkeys (hotkeys only listen; nothing is sent to the game)
  - **Subtitle overlay**: optional on-screen captions over the game
- **Packaged as a .exe** with PyInstaller. Models run separately in Ollama, so the app stays small and backends stay swappable.

Play in **borderless windowed** mode for reliable capture and overlay support.

## Tech stack

- **Python 3.12**
- **Game detection:** `psutil`, Win32 window APIs
- **Window capture:** `windows-capture` (Windows Graphics Capture), `mss` fallback
- **Vision:** OpenCV, Qwen2.5-VL / Moondream (via Ollama)
- **Language:** small local LLMs via Ollama, or any OpenAI-compatible API
- **Speech:** Kokoro / Piper
- **Config:** YAML
- **Packaging:** PyInstaller

## Project structure (planned)

```
Game-Commentor/
├── main.py                       # Entry point (command line)
├── commentator/                  # The engine: one folder per part
│   ├── engine/                   # Runs the pipeline; start / stop / mute / switch persona
│   ├── detection/                # Which game is running + find its window
│   ├── capture/                  # Window capture
│   ├── watcher/                  # OpenCV checks + vision model → game state
│   ├── rules/                    # Compare states → events (priority + expiry)
│   ├── director/                 # Priority queue, expiry, interrupts
│   ├── narrator/                 # LLM → commentary line
│   ├── voice/                    # TTS + stoppable playback
│   ├── memory/                   # Fight memory, session memory, context builder (all in RAM)
│   ├── backends/                 # Swappable model adapters (Ollama, OpenAI-compatible, Claude, Gemini, Kokoro, ...)
│   └── ui/                       # Front ends: command line, tray app, subtitle overlay
├── config/                       # Everything you can swap without touching code
│   ├── games/                    # How to recognise each game, what to track, what counts as an event
│   │   ├── generic.yaml          # Works in any game (vision model only)
│   │   ├── elden_ring.yaml       # Process match, HUD regions, tracked stats, events
│   │   └── refs/                 # Reference images (YOU DIED, ENEMY FELLED)
│   ├── personas/                 # How the commentator talks
│   │   ├── sports_caster.yaml
│   │   └── dark_narrator.yaml
│   └── backends.yaml             # Which model runs each stage (local or API)
├── packaging/                    # PyInstaller build → GameCommentator.exe
├── tests/
├── docs/
│   ├── DESIGN.md                 # What and why: architecture, key decisions, principles
│   ├── IMPLEMENTATION.md         # How: module-by-module guide, interfaces, build status
│   └── architecture.png          # Architecture diagram
├── .gitignore
└── README.md
```

- **`commentator/`**: each pipeline stage is its own folder (a Python package), so every part stays small and easy to find as the code grows.
- **`commentator/ui/`**: front ends only call the engine's controls, so the command line, tray app and overlay share all the same logic.
- **`config/`**: game, persona and backend files are plain YAML. Adding a game, a new commentary style or a different model means changing a file here, not writing code.
- **`docs/`**: short, focused docs so humans and AI agents can get context on one area without reading the whole codebase.

## Getting started

Requires Python 3.12+.

```bash
git clone git@github.com:Welsby/Game-Commentor.git
cd Game-Commentor
python main.py
```

## Roadmap

1. Test the vision model and HUD reading on Elden Ring boss-fight screenshots
2. Game detection + window capture → game state printed in the terminal
3. Rules → events
4. Director + narrator → commentary lines (text only)
5. Text-to-speech with stoppable playback and interrupts
6. Fight memory, then session memory and context builder
7. Game configs, personas, swappable backends, session record/replay, tests
8. Tray app with global hotkeys
9. Subtitle overlay
10. Package as a .exe and publish on GitHub Releases

**Stretch goals:** two casters (play-by-play + colour commentary), OBS browser source, auto-clipped highlights.

