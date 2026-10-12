# Game Commentator

A real-time AI commentator for your own gameplay. It watches your screen, works out what's happening, and narrates it out loud, like a sports caster calling a title fight. It reacts instantly to big moments and stays quiet when nothing is going on.

The first target is **Elden Ring boss fights**.

> **Status:** early setup. Only `main.py` exists so far; the structure below is the planned layout.

---

## What it does

- Captures your screen while you play
- Reads the game's HUD (your HP, boss HP, "YOU DIED", "ENEMY FELLED") with fast pixel checks
- Uses a vision model to describe what's happening ("boss leaping into the air", "player summoned spirit ashes")
- Turns changes between frames into **events** such as `big_hit_landed`, `low_hp`, `you_died`, `boss_felled`
- Decides **when** to speak and **what** to talk about using a priority queue:
  - `filler` / `normal` lines are dropped if something is already playing
  - `high` events are queued to play next
  - `critical` events (death, boss felled) **interrupt** whatever is being said
  - every line has an expiry time, so stale reactions are never spoken
- Writes a short commentary line with an LLM and speaks it with text-to-speech
- Remembers your attempts on each boss: *"Attempt 23 on Malenia… boss at 18%, the furthest he's ever got!"*

## Outcome

- **Live, spoken commentary** on Elden Ring boss fights that feels responsive, not robotic
- **Runs fully local and free** on a consumer GPU alongside the game (developed on an RTX 4070 Ti); no API keys required
- **Modular by design**: three independent parts can be swapped without touching the engine:
  - **Game config**: what to track and what counts as an event (`games/*.yaml`)
  - **Persona**: how it talks: style, example lines, voice, chattiness (`personas/*.yaml`)
  - **Model backends**: local (Ollama, Kokoro, Piper) or API (OpenAI-compatible, Claude, Gemini, ElevenLabs)
- **Read-only**: it only looks at the screen and never sends inputs to the game

## How it works

![Architecture diagram](docs/architecture.png)

```
Screen capture ──► OpenCV checks (fast, ~100ms) ──┐
               └─► Vision model (~1s) ────────────┴─► Compare + rules ──► Director ──► Narrator LLM ──► TTS ──► Speaker
                                                      (state → events)   (priority,    (event → line)  (voice)  (stoppable)
                                                                          expiry,
                                                                          interrupts)
```

| Stage | Type | Job |
|---|---|---|
| Screen capture | Code | Grab frames from the game |
| OpenCV checks | Code | Read HP bars, boss bar, death and victory screens |
| Vision model (VLM) | Model | Describe the scene and anything unusual |
| Compare + rules | Code | Compare the current state with the last one and emit events with priority and expiry |
| Director | Code | Pick which event to narrate and when; handle queueing and interrupts |
| Narrator | Model | Write one short line in the persona's style |
| TTS | Model | Turn the line into speech |
| Speaker | Code | Play audio in chunks so it can be cut off for critical events |

**Design principle:** *models for perception and language, code for decisions.*

## Tech stack

- **Python 3.12**
- **Screen capture:** `mss` / `dxcam`
- **Vision:** OpenCV, Qwen2.5-VL / Moondream (via Ollama)
- **Language:** small local LLMs via Ollama, or any OpenAI-compatible API
- **Speech:** Kokoro / Piper
- **Config:** YAML

## Project structure (planned)

```
Game-Commentor/
├── main.py                   # Entry point
├── commentator/
│   ├── capture.py            # Screen capture
│   ├── watcher.py            # OpenCV checks + vision model → game state
│   ├── rules.py              # Compare states → events (priority + expiry)
│   ├── director.py           # Priority queue, expiry, interrupts
│   ├── narrator.py           # LLM → commentary line
│   ├── voice.py              # TTS + stoppable playback
│   ├── memory.py             # Attempt history per boss
│   └── backends/             # Swappable model adapters (Ollama, OpenAI-compatible, Claude, Gemini, Kokoro, ...)
├── games/
│   ├── generic.yaml          # Works in any game (vision model only)
│   ├── elden_ring.yaml       # HUD regions, tracked stats, events
│   └── refs/                 # Reference images (YOU DIED, ENEMY FELLED)
├── personas/
│   ├── sports_caster.yaml
│   └── dark_narrator.yaml
├── tests/
├── docs/
│   └── architecture.png      # Architecture diagram
├── .gitignore
└── README.md
```

## Getting started

Requires Python 3.12+.

```bash
git clone git@github.com:Welsby/Game-Commentor.git
cd Game-Commentor
python main.py
```

## Roadmap

1. Test the vision model and HUD reading on Elden Ring boss-fight screenshots
2. Live capture → game state printed in the terminal
3. Rules → events
4. Director + narrator → commentary lines (text only)
5. Text-to-speech with stoppable playback and interrupts
6. Game configs, personas, swappable backends, session record/replay, tests

**Stretch goals:** two casters (play-by-play + colour commentary), OBS subtitle overlay, auto-clipped highlights.

