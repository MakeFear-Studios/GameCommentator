# Design

**What** Game Commentator is, **how** it works, and **why** it's built this way.

- For *who builds what* (tracks, tasks, milestones, status), see [IMPLEMENTATION.md](IMPLEMENTATION.md).
- For the overview, see the [README](../README.md).

---

## 1. Goals

- **Live, spoken commentary** on your own gameplay that feels responsive: it reacts to big moments within a second or two and stays quiet when nothing is happening.
- **First target: Elden Ring boss fights.** The design stays game-agnostic.
- **Local and free by default.** Runs on a consumer GPU alongside the game (developed on an RTX 4070 Ti, 12 GB). Any model can be swapped for an API.
- **Modular.** The game, the commentary style and the models are independent, swappable configs.
- **A Windows companion app, never a mod.** Read-only: it looks at the game window and never sends inputs to the game.
- **Easy to install.** One installer that also sets up the local model runtime.

## 2. Principles

1. **Models see and judge; code decides when to speak.** The vision LLM decides what happened and how important it is. Code validates its output and owns all timing (priority stack, expiry, interrupts).
2. **Modular throughout.** Three independent swappable parts: game config, persona, model backends. Adding a game, a voice or a model is a config change, not a code change.
3. **The engine is separate from how it's launched.** Command line, tray app, overlay and dev dashboard are all front ends that call the same engine controls.
4. **Everything observable.** Every stage publishes what it does to an event bus, so behaviour can be logged, inspected and replayed.
5. **Small prompts for small models.** Context passed to models is pre-summarised by code and kept short.

## 3. Architecture

```
Detect game ──► Window capture ──► Vision LLM ──► Validation ──► Director ──► Narrator LLM ──► TTS ──► Speaker
                                   (frame + game     (schema,      (priority     (event +        (voice)  (chunked,
                                    config + last     dedupe,       stack,        memory →                 stoppable)
                                    state → JSON)     TTL)          expiry,       one line)
                                                                    interrupts)        ▲
                                                                        │              │
                                                                        └─► Memory ────┘
                                                                            (fight + session, in RAM)

Event bus ◄── every stage publishes ──► Dev logger (logs/<session>/events.jsonl) ──► Dev dashboard
```

| Stage | Type | Package |
|---|---|---|
| Game detection | Code | `commentator/detection/` |
| Window capture | Code | `commentator/capture/` |
| Vision LLM + validation | Model + code | `commentator/vision/` |
| Director | Code | `commentator/director/` |
| Narrator | Model | `commentator/narrator/` |
| Voice (TTS + playback) | Model + code | `commentator/voice/` |
| Memory | Code | `commentator/memory/` |
| Event bus, dev logger | Code | `commentator/events/` |
| Engine (wiring, controls) | Code | `commentator/engine/` |
| Front ends | Code | `commentator/ui/` |
| Shared types, config, paths | Code | `commentator/core/` |
| Model adapters | Code | `commentator/backends/` |

Stages run in their own threads, connected by `queue.Queue` pipes (see [IMPLEMENTATION.md § Contracts](IMPLEMENTATION.md#3-contracts)).

---

## 4. Stages

### 4.1 Game detection

The app captures the **game's window**, not "the screen", so it doesn't matter which monitor the game is on.

- Each game config says how to recognise its game:
  ```yaml
  match:
    process: eldenring.exe        # most reliable
    window_title: "ELDEN RING"    # fallback
  ```
- On startup and every few seconds: list running processes (`psutil`), check each config's `match`, load the matching config. No match: use `generic.yaml` on a window the user picks.
- Manual override from the tray menu or `--game` / `--window`.
- Track the window's position and size about every second; pause commentary when the game isn't focused.
- Make the process **DPI-aware** at startup, or window positions are wrong under Windows display scaling.

### 4.2 Window capture

| Library | How | Role |
|---|---|---|
| `windows-capture` (Windows Graphics Capture) | Captures one specific window, wherever it is, even partly covered | **Default** |
| `mss` | Captures a screen region | Fallback |

- Keep only the **newest frame**. If the vision LLM is busy, skipped frames are discarded, so commentary never falls behind.
- Play in **borderless windowed** mode: capture can fail in exclusive fullscreen, and the overlay needs it.

### 4.3 Vision LLM (the "eyes and judge")

One multimodal model looks at the frame and decides what happened.

**Input**
- The newest frame, downscaled (about 768 px wide) to keep it fast.
- The game config's **state fields** and **event catalogue** (each event's description, priority and expiry).
- The **previous state**, so it can judge changes ("boss HP dropped from 27% to 18%").

**Output:** structured JSON, forced into a schema generated from the game config (Ollama's `format` / OpenAI-style structured output):

```json
{
  "state":  { "player_hp": 34, "boss_hp": 18, "boss_name": "Margit, the Fell Omen", "flasks": 1 },
  "events": [ { "type": "big_hit_landed", "priority": "high", "detail": "Charged greatsword hit, boss 27% to 18%" } ],
  "scene":  "Margit leaps into the air with a golden hammer while the player rolls away"
}
```

**Validation (code)**
- Reject anything that doesn't match the schema.
- Drop event types not in the game config; enforce the priority enum.
- Attach each event's **TTL** from the config.
- **De-duplicate** with a per-event `cooldown`: "YOU DIED" stays on screen for several frames but is reported once.

**Cadence:** as fast as the model allows, about once a second with a 3B model on a 4070 Ti.

**Trade-offs** (this replaced an earlier OpenCV + rules design): slower (about 1–2 s to notice a death instead of about 1 ms) and less consistent than pixel checks. Mitigated by the schema, validation, cooldowns, the previous state in the prompt, and choosing the model from a measured spike. A fast-path check for critical events can be added later without changing the contracts.

### 4.4 Director (when to speak, and what about)

The director holds incoming events on a **priority stack**: one newest-first (LIFO) stack per priority level.

| Priority | Examples | Behaviour |
|---|---|---|
| `critical` | Boss appeared, you died, boss felled | **Interrupts** current speech; narrated immediately |
| `high` | Big hit, heavy damage, low HP, phase two | Waits on its stack; the newest is spoken when the voice is free |
| `normal` | Healed, notable moment | Dropped if something is already playing |
| `filler` | Scene update | Only if nothing else is playing |

**Why a stack:** commentary should be about what just happened. If several same-priority events pile up while the caster is talking, a queue would pick the oldest (stale); a stack picks the newest. Priority still always beats recency.

```
critical: [ ]
high:     [ took_heavy_damage, low_hp, big_hit_landed ← top ]   ← voice is free: take this one
normal:   [ healed ← top ]
filler:   [ scene_update ← top ]
```

Rules:
- Take the top of the **highest non-empty stack**; the rest of that stack goes to the narrator as context, so one line can cover several events.
- **Expired events are dropped** whenever a stack is read; each stack keeps only its few newest events.
- **Check expiry twice:** before asking the narrator to write a line, and again before speaking it (writing takes 0.3–1 s).
- **Cooldowns** (from the persona) stop it talking non-stop. Silence is fine.

Example:
```
t=0.0  speaking a filler line
t=1.0  took_heavy_damage (high, ttl 2.5) → pushed
t=2.0  big_hit_landed    (high, ttl 2.5) → pushed
t=2.8  voice free → take big_hit_landed (newest high); took_heavy_damage goes along as context
t=5.0  you_died (critical) → narrate now → interrupt current speech
```

### 4.5 Narrator (the "mouth")

Gets the chosen event plus context, **not** the screenshot:
```
Event: big_hit_landed (boss 27% → 18%, player HP 34)
Also recently: took_heavy_damage
Scene: "Margit leaps into the air while the player rolls away"
Memory: attempt 7 on Margit; struggling with the delayed slam
Your last lines: "He's in trouble…", "One flask left!"
→ write one short line in the persona's style
```
→ *"ON 34 HP?! Margit's at 18%, the furthest he's got all night!"*

- Sending its recent lines stops repetition.
- Lines are kept short (persona word limit, trimmed in code).
- The line keeps its event's priority and expiry.

### 4.6 Voice

- TTS: **Kokoro** (`kokoro-onnx`, default) or **Piper**, locally; ElevenLabs / OpenAI TTS via API.
- Audio plays in **small chunks** so it can be cut off mid-sentence for critical events (target: under about 200 ms).
- On interrupt, the persona's interrupt sound plays first ("OH—" / "…").

### 4.7 Memory (in RAM only)

Nothing is saved to disk; memory lasts while the app runs and reaches the narrator as short, pre-summarised facts.

**Fight memory** (reset per boss fight): boss, attempt number, deaths, **moves that hit you** (from vision `detail` on damage events), **your weapon and the boss's**, streaks and patterns, flasks, summons, phase, the boss's lowest HP.

**Session memory** (whole play session): a small **capped** rolling summary of progress this session (bosses beaten, region from the area-name banner), key moments, a skill read (improving or struggling) and habits. Older or less important facts drop off so it always fits in the context window.

```
events ──► Fight memory ──► summarised at fight end ──► Session memory
                │                                            │
                └──────────► Context builder ◄───────────────┘
                                   │ a few short facts
                                   ▼
                              Narrator LLM
```

Code calculates the stats; the LLM only talks about them.

---

## 5. Configuration

Three independent kinds of YAML under `config/`:

| File | Decides |
|---|---|
| `config/games/*.yaml` | **What happened:** how to recognise the game, state fields, event catalogue |
| `config/personas/*.yaml` | **How it's said:** style, example lines, voice, chattiness |
| `config/backends.yaml` | **Which models:** local or API, per stage |

### Game config

```yaml
# config/games/elden_ring.yaml
match: { process: eldenring.exe, window_title: "ELDEN RING" }

state_fields:
  player_hp: { type: int, describe: "Player red health bar (top left) as a percentage" }
  boss_hp:   { type: int, describe: "Boss health bar (bottom centre) as a percentage, null if no boss" }
  boss_name: { type: str, describe: "Name above the boss bar" }
  flasks:    { type: int, describe: "Number on the flask icon (bottom left)" }
  region:    { type: str, describe: "Area name banner text if visible" }

events:
  boss_appeared:     { priority: critical, ttl: 5.0, cooldown: 60, look_for: "A boss health bar and name appear at the bottom" }
  you_died:          { priority: critical, ttl: 5.0, cooldown: 10, look_for: "Large red 'YOU DIED' text in the centre" }
  boss_felled:       { priority: critical, ttl: 6.0, cooldown: 30, look_for: "Gold 'ENEMY / GREAT ENEMY / DEMIGOD FELLED' text" }
  big_hit_landed:    { priority: high,     ttl: 2.5, cooldown: 2,  look_for: "Boss health drops sharply compared with the last state" }
  took_heavy_damage: { priority: high,     ttl: 2.5, cooldown: 2,  look_for: "Player health drops sharply; describe the attack" }
  low_hp:            { priority: high,     ttl: 3.0, cooldown: 8,  look_for: "Player health below about a quarter" }
  phase_two:         { priority: high,     ttl: 4.0, cooldown: 60, look_for: "Boss transforms or changes attack style" }
  out_of_flasks:     { priority: high,     ttl: 4.0, cooldown: 30, look_for: "Flask count reaches zero" }
  healed:            { priority: normal,   ttl: 3.0, cooldown: 3,  look_for: "Player drinks a flask; health rises" }
  scene_update:      { priority: filler,   ttl: 6.0, cooldown: 8,  look_for: "Anything worth a casual remark" }
```

- The vision prompt and JSON schema are **generated from this file**.
- `generic.yaml` has no `match` and a minimal catalogue, so it works in any game.
- Optional `examples/` frames can be shown to the vision LLM as references.

### Persona config

```yaml
# config/personas/sports_caster.yaml
name: "Sports Caster"
style: >
  You are an over-the-top boxing commentator calling an Elden Ring boss fight
  like a title bout. Short, loud, punchy. Max 15 words.
voice: { tts_voice: am_michael, speed: 1.15 }
chattiness: { min_gap_s: 1.5, use_filler: true }
interrupt_sound: "OH—"
examples:
  big_hit_landed: ["HUGE hit! The crowd is on its feet!", "Right on the chin!"]
  you_died:       ["DOWN HE GOES! The ref is counting!", "That's a knockout, folks."]
  boss_felled:    ["IT'S OVER! A NEW CHAMPION!"]
```

```yaml
# config/personas/dark_narrator.yaml
name: "Dark Narrator"
style: >
  You are a solemn, poetic narrator in the style of a dark fantasy epic.
  Speak slowly, with gravitas and dry irony. Max 20 words.
voice: { tts_voice: bm_george, speed: 0.9 }
chattiness: { min_gap_s: 4.0, use_filler: false }
interrupt_sound: "…"
event_overrides:
  healed: { ignore: true }
examples:
  you_died:    ["And so the Tarnished fell… as foretold.", "Death, once more. How familiar."]
  boss_felled: ["The great one is felled. Yet the Lands Between remain unmoved."]
```

Example lines matter most for small local models: a few examples get the tone right far better than a description alone. Two casters can be supported later with a `casters:` list in one persona.

### Backends config

One interface per model type (`VisionModel`, `TextModel`, `SpeechModel`). One **OpenAI-compatible adapter** covers Ollama, LM Studio, vLLM, OpenRouter, Groq and OpenAI; separate adapters only for Claude and Gemini.

| Stage | Local | API |
|---|---|---|
| Vision LLM | Qwen2.5-VL 3B / 7B, Gemma 3, Moondream (Ollama) | GPT-4o-mini, Claude Haiku, Gemini Flash |
| Narrator | The same model, or a 3–8B text model (Ollama) | Same as above |
| TTS | Kokoro, Piper | ElevenLabs, OpenAI TTS |

**GPU budget (4070 Ti, 12 GB, shared with the game):** game about 4–8 GB; a 3–4B model about 2.5 GB (start here); 7–8B about 5 GB; Kokoro under 1 GB. Using one model for both vision and narration saves memory.

---

## 6. Running the app

**Engine and front ends.** The engine wires the stages and exposes `start`, `stop`, `mute`, `set_persona` and `set_game`. Front ends only call those:

| Front end | Use |
|---|---|
| Command line (`python main.py --game elden_ring --persona dark_narrator`) | Development |
| Tray app with global hotkeys (listen only) | Daily use while gaming |
| Subtitle overlay (transparent, click-through) | Demo videos and clips |
| Dev dashboard (local web page) | Debugging and tuning |

**Why not a mod:** mods mean injecting code into the game (needs anti-cheat off, breaks on updates, tied to one game). A companion app that reads the window works with any game.

### Dev mode (`--dev`)

- Every stage publishes small structured events to the **event bus**:
  ```json
  {"t": 12.43, "stage": "director", "type": "event_dropped", "data": {"event": "healed", "reason": "expired"}}
  ```
- The **dev logger** writes everything to `logs/<session>/events.jsonl` (JSON Lines), plus a sampled frame every few seconds. Nothing is written without `--dev`.
- The **dev dashboard** (local web page, WebSocket) follows events live, or opens an old log and replays it.
- The same logs **replay** recorded vision events through the director, narrator and voice for testing without the game.
- Stages don't know the dashboard exists, so it can change freely. What it shows is decided once real logs exist (candidates: frame next to vision JSON, event timeline by priority, director stacks with expiry countdowns, narrator prompts and replies, memory, latency per stage).

---

## 7. Packaging, installer and first run

The app contains **the code, not the models.** Models are several GB and must stay swappable, so they live in Ollama.

```
Program Files\GameCommentator\       ← installed app (PyInstaller one-folder)
├── GameCommentator.exe
└── config\                          ← editable YAML

%LOCALAPPDATA%\GameCommentator\      ← per-user data
├── models\                          ← TTS voice files (Kokoro)
├── logs\                            ← dev-mode logs
└── settings.yaml                    ← user choices

Ollama (separate program)            ← installed by our installer if missing; holds the vision/text models
```

**Installer** (`GameCommentatorSetup.exe`, Inno Setup):
1. Install the app files.
2. If Ollama is missing, download the official installer and run it silently. Never remove Ollama on uninstall.
3. Optional checkbox: download the AI models now (about 3–4 GB).
4. Shortcuts, then launch.

**First-run check** (every start): Ollama running? Models pulled? Voice files present? Offer to download what's missing, or switch to an API backend.

**Build details:** `kokoro-onnx` instead of PyTorch (keeps the build small); PyInstaller **one-folder** mode (faster start, fewer antivirus false positives); one path helper for config, user data and model dirs that works from source and from the .exe.

**Releases:** pushing a version tag runs GitHub Actions: tests → PyInstaller → Inno Setup → installer attached to a GitHub Release.

**Expect:** a SmartScreen warning for the unsigned installer ("More info → Run anyway"), occasional antivirus false positives. Licences: Ollama MIT (include its notice), Kokoro Apache 2.0; models are pulled by the user from Ollama's library (check each model's licence; some are research or non-commercial only).

---

## 8. Elden Ring notes

- Key events: boss appeared, you died, boss felled (critical); big hit, heavy damage, low HP, phase two, out of flasks (high); healed (normal); scene update (filler).
- Some bosses have **two health bars** (e.g. Valiant Gargoyles); `boss_hp` may need to be a list.
- The **area-name banner** on entering a region gives session memory the current region.
- Play in **borderless windowed** mode.

---

## 9. Testing strategy

- **Director:** fake clock; tests for newest-first within a level, priority beating recency, the stack cap, expiry and interrupts.
- **Vision validation:** recorded model outputs → schema checks, unknown events dropped, cooldown de-duplication.
- **Prompt and schema generation:** snapshot tests from the game config.
- **Memory:** fight summaries and the session memory size cap, from recorded event logs.
- **Detection:** match logic against fake process and window lists.
- **End to end without the game:** replay a recorded `events.jsonl` through director, narrator and voice.
- **Measure:** event → voice latency, per stage, in dev mode.

## 10. Open questions

- Two casters in v1, or later?
- Is vision-only death detection fast enough, or do critical events need a fast-path check?
- Which vision model is the best default? (Decided by spike A1.)

## 11. Decisions

The decision log, with reasons and trade-offs, lives in [IMPLEMENTATION.md § Decision log](IMPLEMENTATION.md#8-decision-log).
