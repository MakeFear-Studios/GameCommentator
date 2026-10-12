# Implementation Plan

How **Welsby** and **Nathan** build Game Commentator in parallel.

This document is the working plan: who owns what, the contracts between the two halves, the task list for each track, the integration milestones, and how we work together. It's written so a human or an AI agent can pick up context by reading **Contracts** plus **their own track**, without reading the whole codebase.

For *what* the project is and *why* it's designed this way, see the [README](../README.md).

**Status legend:** ⬜ not started · 🟨 in progress · ✅ done

---

## 1. The split

The pipeline splits cleanly at the **event stream**. Track A produces `GameEvent`s from the game; Track B turns `GameEvent`s into speech. Neither track waits on the other: B builds against fake and recorded events, and A checks its output in the dev log.

```
            Track A: Welsby ("see and ship")                Track B: Nathan ("think and speak")
 ┌────────────────────────────────────────────────┐   ┌──────────────────────────────────────────────────┐
 Detect game ─► Window capture ─► Vision LLM ─► validate ═══► Director ─► Narrator LLM ─► TTS ─► Speaker
                                                 GameEvents       ▲            ▲
                                                 + events.jsonl   └─ Memory ───┘
 └────────────────────────────────────────────────┘   └──────────────────────────────────────────────────┘
```

| | **Welsby: Track A, "see and ship"** | **Nathan: Track B, "think and speak"** |
|---|---|---|
| Owns | `vision/`, `logs/`, `ui/dev/`, `config/games/`, `packaging/`, `.github/` | `detection/`, `capture/`, `director/`, `narrator/`, `voice/`, `memory/`, `engine/`, `ui/` (cli, tray, overlay), `config/personas/` |
| Backends | Vision + text LLM adapter (OpenAI-compatible / Ollama) | TTS adapters (Kokoro, Piper); API adapters (Claude, Gemini) |
| Needs Elden Ring? | Yes | No: works from fake and recorded events |
| ML / AI | Multimodal LLM: prompts from config, structured output, validation | Narrator prompting, personas, memory and context design |
| Engineering | Windows APIs (process/window detection, GPU capture), event bus, logging | Real-time scheduling (priority stack, expiry, interrupts), streaming audio |
| UI | Dev dashboard (local web app, live + replay) | Tray app with hotkeys, subtitle overlay |
| DevOps | PyInstaller, Inno Setup installer, GitHub Actions releases | Test scenarios, fake clock, replay-driven tests |

Both tracks get an AI component, a systems component, a UI and a testing or DevOps story, so each of us has a full-stack piece to talk about.

**Exception:** Nathan owns startup, game detection and window capture (A2), even though they sit at the start of Track A in the diagram above. See the decision log.

**Shared:** `core/` (contracts) is owned by both. Changes go through a pull request labelled `contract` and need both approvals.

---

## 2. Phase 0: Foundations (together)

Goal: agree the contracts and set up the repo so we can split. Split the tasks, then review each other's PRs.

| ID | Task | Owner | Status |
|---|---|---|---|
| 0.1 | Merge `initial-setup` into `main`, make `main` the default branch, protect it (PRs + 1 review required). *Merged; default branch and protection still to set in GitHub settings.* | Welsby | 🟨 |
| 0.2 | `pyproject.toml` (`requires-python = ">=3.13,<3.14"`) with dependency groups (`dev`, `vision`, `voice`, `ui`, `build`), ruff, pytest, pre-commit | Welsby | ⬜ |
| 0.3 | GitHub Actions CI: lint + tests on every PR (Windows runner, Python 3.13) | Welsby | ⬜ |
| 0.4 | `logs/`: log bus (`publish` / `subscribe`), dev logger writing `logs/<session>/`, log reader, Python `logging` bridge, `--dev` / `GAME_COMMENTATOR_DEV=1` in `main.py` with crash recording and .exe-aware log location, tests. See `commentator/logs/README.md`. *0.5 extends `main.py`'s parser; 0.7 takes over `default_log_root()`.* | Welsby | ✅ |
| 0.5 | Package skeleton: every folder from the README with `__init__.py`; `main.py` parses `--dev`, `--game`, `--persona` | Nathan | 🟨 |
| 0.6 | `core/types.py`: `Priority`, `GameEvent`, `GameState`, `VisionResult`, `CommentaryLine` (see Contracts) | Nathan | ⬜ |
| 0.7 | `core/config.py`: load and validate game, persona and backend YAML (pydantic); `core/paths.py` for config, user data and models dirs | Nathan | ⬜ |
| 0.8 | `backends/base.py`: `VisionModel`, `TextModel`, `SpeechModel` protocols, plus fake implementations for tests | Nathan | ⬜ |
| 0.9 | `CODEOWNERS`, issue templates, GitHub Project board with one issue per task ID | Both | ⬜ |

**Done when (M0):** CI is green on `main`, and `python main.py --dev` writes an `events.jsonl` containing a `session_started` event.

---

## 3. Contracts

These are the seams between the tracks. Code against them; change them only through a `contract` PR.

### Shared types (`commentator/core/types.py`)

```python
class Priority(StrEnum):
    FILLER = "filler"      # only after a quiet spell
    NORMAL = "normal"      # waits for the voice to be free; expires quickly
    HIGH = "high"          # waits on its stack; may soft-interrupt a long, lower-priority line
    CRITICAL = "critical"  # hard-interrupts current speech

@dataclass(frozen=True)
class GameEvent:
    type: str              # must exist in the game config's event catalogue
    priority: Priority     # set by the vision stage (model picks from the game config, validation enforces it); the director only stacks it
    ttl: float             # seconds; from the game config
    detail: str            # short description from the vision LLM
    t: float               # time.monotonic() when the screenshot was captured (Frame.t), not when it arrived
    source: str = "vision" # "vision" | "replay" | "fake"
    # .expires_at = t + ttl

@dataclass(frozen=True)
class GameState:
    fields: Mapping[str, Any] = {}  # what this screenshot shows, e.g. {"player_hp": 34}; keys from the game config
    scene: str | None = None        # one-sentence description of this screenshot

@dataclass(frozen=True)
class VisionResult:
    """What the vision stage saw in ONE screenshot. No history: the director keeps that."""
    state: GameState
    events: tuple[GameEvent, ...]
    frame_t: float         # Frame.t of the screenshot
    latency_ms: float = 0  # how long vision took

class VisionSink(Protocol):
    def submit(self, result: VisionResult) -> None: ...   # implemented by the director; must not block
```

### Backend protocols (`commentator/backends/base.py`)

```python
class VisionModel(Protocol):
    def analyse(self, image_png: bytes, prompt: str, json_schema: dict) -> dict: ...

class TextModel(Protocol):
    def generate(self, system: str, prompt: str, max_tokens: int = 60) -> str: ...

class SpeechModel(Protocol):
    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]: ...  # audio chunks
```

### Event sources (the hand-off between tracks)

The vision stage pushes each validated `VisionResult` to a `VisionSink` (the director). For testing without the game, sources produce the same results:

```python
class VisionSource(Protocol):
    def results(self) -> Iterator[VisionResult]: ...   # in capture-time order
```

Implementations: `ReplayVisionSource` (reads `events.jsonl`, Track A), `FakeVisionSource` (scripted scenarios, Track B, in `commentator/director/sim.py`). A small pump feeds a source into a `VisionSink` at the recorded pace.

### Frames: capture → vision (`commentator/core/types.py`)

```python
@dataclass(frozen=True)
class Frame:
    image: np.ndarray      # height x width x 4, BGRA, uint8
    t: float               # time.monotonic() when captured

class FrameSink(Protocol):
    def submit(self, frame: Frame) -> None: ...   # must not block
```

Capture calls `submit` from its tracking thread (up to 4 times a second) with each new frame while the game is focused. Paused frames and frames from before a resume are never sent. The vision stage implements `FrameSink`: keep only the newest frame and analyse it when the model is free. Until then, `NullFrameSink` accepts frames and does nothing. Exceptions from `submit` are logged as `sink_error` and don't stop capture.

### Director → narrator → voice (`commentator/core/types.py`)

The director owns all timing. It gathers context itself (stacks, state history, spoken lines, memory) and sends the narrator one request at a time; the narrator only writes.

```python
class Mode(StrEnum):
    REACTION = "reaction"          # critical moment: very short
    PLAY_BY_PLAY = "play_by_play"  # high / normal events during action
    COLOUR = "colour"              # quiet spell: analysis and stats from memory
    FILLER = "filler"              # scene remark when nothing else is happening

class Delivery(StrEnum):
    HARD_INTERRUPT = "hard_interrupt"  # cut current speech at the next audio chunk
    SOFT_INTERRUPT = "soft_interrupt"  # after the current sentence
    QUEUED = "queued"                  # when the voice is free

@dataclass(frozen=True)
class NarrationRequest:
    id: int
    event: GameEvent | None          # top of the highest non-empty stack; None for a colour line in a quiet spell
    related: tuple[GameEvent, ...]   # rest of that stack, newest first
    counts: Mapping[str, int]        # event type -> times seen (merged repeats), e.g. {"big_hit_landed": 3}
    state: GameState                 # latest state
    state_change: Mapping[str, tuple[Any, Any]]  # field -> (at the last line, now), e.g. {"boss_hp": (27, 18)}
    memory: tuple[str, ...]          # short facts from memory (B4); empty until it exists
    recent_lines: tuple[str, ...]    # last few spoken lines, to avoid repeats
    mode: Mode
    priority: Priority               # of the chosen event (FILLER for colour lines)
    max_words: int                   # from mode, persona and backlog
    delivery: Delivery
    created_at: float                # monotonic
    expires_at: float                # monotonic; don't speak after this

class Narrator(Protocol):
    def request(self, req: NarrationRequest) -> None: ...   # must not block; replies via the director's inbox

@dataclass(frozen=True)
class CommentaryLine:
    request_id: int
    text: str | None                 # None = "nothing to say" (allowed for colour / filler)
    event: GameEvent | None
    priority: Priority
    mode: Mode
    delivery: Delivery
    created_at: float
    expires_at: float

class SpeechState(StrEnum):
    STARTED = "started"; ENDING = "ending"; FINISHED = "finished"; INTERRUPTED = "interrupted"

@dataclass(frozen=True)
class SpeechStatus:
    line_id: int                     # CommentaryLine.request_id
    state: SpeechState
    t: float                         # monotonic
    duration_s: float | None = None  # on STARTED: exact audio length from TTS
    remaining_s: float | None = None # on ENDING / progress updates

class Voice(Protocol):
    def play(self, line: CommentaryLine) -> None: ...      # QUEUED: voice is free; SOFT_INTERRUPT: after the current sentence
    def interrupt(self, line: CommentaryLine) -> None: ...  # hard: cut now, interrupt sound, then the line
```

The narrator and voice report back through `Director.on_line` and `Director.on_speech`, which only queue into the director's inbox. The director re-checks every line (expiry, superseded, still true for the latest state) before passing it to voice.

### Log bus and log format (`commentator/logs/`) ✅ implemented

```python
from commentator.logs import bus

bus.publish("director", "event_dropped", {"event": "healed", "reason": "expired"})   # level="info" by default
bus.publish("vision", "model_timeout", {"model": "qwen2.5vl:3b"}, level="warning")
bus.subscribe(callback)            # dev logger, dashboard, tests; callback(LogEvent) runs on the bus thread
```

- **Observation only.** Stages pass real data through their own queues; the pipeline must behave the same with no subscribers.
- **Never blocks.** `publish` returns immediately; a background thread delivers. With no subscribers it does nothing.
- **Bounded buffer** (10,000 events): when full, the oldest are dropped and a `logs / events_dropped` warning is emitted with the count.
- **Conventions:** `stage` = package name; `type` = snake_case past tense; `data` = small, flat, JSON-friendly (images go to `DevLogger.save_frame()` and are referenced by path); `level` ∈ `debug | info | warning | error`.
- **Python `logging`:** `capture_python_logging(bus)` forwards library warnings and errors as `stage="log"`, `type="log_record"`.

One JSON object per line in `logs/<session>/events.jsonl` (`t` = seconds since start, monotonic; `seq` = strict order):

```json
{"seq": 42, "t": 12.43, "stage": "director", "type": "event_dropped", "level": "info", "data": {"event": "healed", "reason": "expired"}}
```

The session folder also has `session.json` (format version, start and end wall-clock time, counts, app info) and `frames/`. The first and last events are `logs / session_started` and `logs / session_ended`. `read_log(path)` reads a log back as `LogEvent`s.

Every stage publishes its key decisions (frame captured, vision result, event pushed / dropped / spoken, narrator prompt and reply, TTS timing).

### Game config: event catalogue (`config/games/*.yaml`)

```yaml
match: { process: eldenring.exe, window_title: "ELDEN RING" }
state_fields:
  player_hp: { type: int, describe: "Player red health bar (top left) as a percentage" }
  boss_hp:   { type: int, describe: "Boss health bar (bottom centre) as a percentage, null if no boss" }
  boss_name: { type: str, describe: "Name above the boss bar" }
events:
  you_died:       { priority: critical, ttl: 5.0, cooldown: 10, look_for: "Large red 'YOU DIED' text in the centre" }
  boss_felled:    { priority: critical, ttl: 6.0, cooldown: 30, look_for: "Gold 'ENEMY FELLED' / 'GREAT ENEMY FELLED' text" }
  big_hit_landed: { priority: high,     ttl: 2.5, cooldown: 2,  look_for: "Boss health drops sharply compared with the last state" }
  healed:         { priority: normal,   ttl: 3.0, cooldown: 3,  cancels: [low_hp, out_of_flasks], look_for: "..." }
context_fields: [boss_name]   # if one of these changes, lines written before are stale
```

The vision prompt and the JSON schema are **generated from this file**, so adding an event means editing YAML only. Two optional keys are for the director: `cancels` (seeing this event makes lines about the listed events untrue, so they're dropped before speaking) and `context_fields`.

### Threading model

One thread per stage, connected by `queue.Queue`s: capture → vision → director → narrator → voice. These queues are just pipes that hand data from one thread to the next in arrival order. The director has **one inbox** for vision results, finished lines and speech status, and runs a **50 ms tick**: it moves incoming events onto its **priority stack** (one newest-first stack per priority level) and owns all timing decisions. Model calls are blocking and run in their stage's thread. All director timing goes through an injectable clock, so tests use a fake one.

---

## 4. Track A: Welsby ("see and ship")

### A1. Vision spike *(start immediately, in parallel with Phase 0)*
| ID | Task | Status |
|---|---|---|
| A1.1 | Install Ollama, pull `qwen2.5vl:3b` (and try `gemma3:4b`, `moondream`) | ⬜ |
| A1.2 | Capture 20–30 boss-fight screenshots: full HP, mid-fight, big hit, low HP, heal, YOU DIED, ENEMY FELLED, phase change | ⬜ |
| A1.3 | `experiments/vision_spike/`: hand-written event catalogue → prompt + JSON schema → run every screenshot, with and without the previous state | ⬜ |
| A1.4 | Write findings: accuracy per event type, latency per model, best image size; pick the default model | ⬜ |

**Done when:** a short results table is in `docs/` and the default model is chosen.

### A2. Detection + capture *(owner: Nathan)*
| ID | Task | Status |
|---|---|---|
| A2.1 | `detection/`: on launch, match running processes / window titles against each game config's `match` (psutil + Win32); if none match, show the "Waiting for game to start" overlay and re-check every few seconds; return to waiting when the game closes | 🟨 |
| A2.2 | DPI awareness at startup; window rect tracking; pause when the game isn't focused | 🟨 |
| A2.3 | `capture/`: `windows-capture` backend with `mss` fallback behind one interface; "newest frame" buffer | 🟨 |
| A2.4 | Publish `frame_captured` (+ sampled frames saved in dev mode) | 🟨 |

**Progress:** working end to end with PICO PARK (`config/games/pico_park.yaml`) as a test game; Elden Ring not checked yet. How the packages work: [`commentator/detection/README.md`](../commentator/detection/README.md), [`commentator/capture/README.md`](../commentator/capture/README.md).

**Done when:** launching the app before the game shows the waiting overlay; starting Elden Ring attaches automatically; `python main.py --dev` logs frames from any monitor and pauses on alt-tab.

### A3. Vision stage
| ID | Task | Status |
|---|---|---|
| A3.1 | `backends/openai_compat.py`: `VisionModel` + `TextModel` via Ollama's OpenAI-compatible API (structured output, `keep_alive`) | ⬜ |
| A3.2 | `vision/prompt.py`: build the prompt and JSON schema from the game config's `state_fields` and `events` | ⬜ |
| A3.3 | `vision/stage.py`: newest frame + previous state → model → `VisionResult` | ⬜ |
| A3.4 | `vision/validate.py`: schema check, drop unknown event types, enforce priority enum, attach TTL, per-event cooldown de-duplication | ⬜ |
| A3.5 | `config/games/elden_ring.yaml` and `generic.yaml` | ⬜ |
| A3.6 | `VisionEventSource` + `ReplayEventSource`; tests using recorded model outputs | ⬜ |

**Done when:** a real boss fight produces sensible events in `events.jsonl` (YOU DIED reported once, big hits caught), and validation is unit-tested.

### A4. Dev dashboard
| ID | Task | Status |
|---|---|---|
| A4.1 | `ui/dev/`: local web server (FastAPI + WebSocket) streaming bus events | ⬜ |
| A4.2 | Replay mode: open an old `events.jsonl` and scrub through it | ⬜ |
| A4.3 | First views: frame + vision JSON side by side; event timeline coloured by priority; per-stage latency | ⬜ |

**Done when:** the dashboard follows a live fight in the browser and replays an old session.

### A5. Packaging and release
| ID | Task | Status |
|---|---|---|
| A5.1 | **Smoke test (right after Phase 0):** PyInstaller one-folder build + minimal Inno Setup installer of the "ready" app | ⬜ |
| A5.2 | First-run check: Ollama running? models pulled? voice files present? Offer download, or switch to an API backend | ⬜ |
| A5.3 | Full installer: detect Ollama, download + silent install if missing, optional model download, shortcuts, never remove Ollama on uninstall | ⬜ |
| A5.4 | `.github/workflows/release.yml`: version tag → tests → build → installer attached to a GitHub Release | ⬜ |

**Done when:** tagging `v0.1.0` produces an installer that works on a clean Windows machine.

---

## 5. Track B: Nathan ("think and speak")

### B1. Fake events + director

Design: [DESIGN.md § 4.4](DESIGN.md#44-director-when-to-speak-and-what-about). How the built package works (wiring, rules, settings, events, simulation): [`commentator/director/README.md`](../commentator/director/README.md). The director takes per-screenshot `VisionResult`s, gathers everything about the past itself, and sends well-timed `NarrationRequest`s.

| ID | Task | Status |
|---|---|---|
| B1.0 | **Contract PR** (`core/types.py`, label `contract`): `Priority`, `GameEvent` (capture-time `t`), `GameState`, `VisionResult`, `VisionSink`, `VisionSource`, `Mode`, `Delivery`, `NarrationRequest`, `Narrator`, `CommentaryLine`, `SpeechStatus`, `Voice`; persona `chattiness` and game `cancels` / `context_fields` loading (`core/config.py`). *Implemented; needs Welsby's approval.* | 🟨 |
| B1.1 | `director/sim.py`: `FakeVisionSource` scenarios ("boss fight ending in death", "burst of hits", "quiet exploration", "heal cancels low HP"), **fake voice** (real durations, hard / soft interrupts, overlaps if misused), **fake narrator** (adjustable delay and words), `Simulation` runner | ✅ |
| B1.2 | `director/stacks.py` + `history.py`: one LIFO stack per priority, capped; expiry from capture time, dropped on every read; same-type merging; repeats of the event being talked about dropped as `covered`; cooldowns; state history and `state_change` | ✅ |
| B1.3 | `speech.py` + `timing.py`: track the current line and its remaining time; running averages of narrator and TTS latency; **prepare the next line** when the time until the voice is free < lead time; lines handed over early by the TTS start-up time so audio starts as the gap ends | ✅ |
| B1.4 | `policy.py`: interrupt levels (hard / soft / none) and limits (`min_on_air_s`, `max_in_window` per `window_s`, only strictly more important events interrupt) | ✅ |
| B1.5 | Re-check before speaking (expired, superseded, `stale_state` from `cancels` or a `context_fields` change); superseded lines' events go back on the stack, stale or expired ones don't | ✅ |
| B1.6 | `context.py`: build `NarrationRequest`s; modes, `max_words` (shorter when a backlog is waiting), pause after critical then a colour reflection, colour / filler after `max_silence_s` | ✅ |
| B1.7 | Publish `event_pushed` / `event_merged` / `event_dropped` (reason: expired, capped, covered, ignored, filler_off) / `event_chosen` / `line_prepared` / `line_sent` / `line_discarded` (superseded, expired, stale_state, late) / `line_ended` / `interrupt` / `silence_chosen`; **fluency report** (`fluency.py`: overlap %, mean gap, longest silence, interrupts per minute, drops) | ✅ |

All director tests run on a fake clock with the fake voice and narrator (`tests/director/`, 100% line coverage of `director.py`).

**Done when:** scenarios produce the right lines at the right times with the right drops, all covered by tests, and the fluency report meets its targets on every scenario for every persona: 0% overlap, mean gap under 2 s (or the persona's `min_gap_s` + 0.5 s if it's slower on purpose), at most 2 non-critical interrupts per minute, critical events requested within one tick of arriving. *Met in tests; not yet run with a real narrator and voice.*

### B2. Narrator + personas
| ID | Task | Status |
|---|---|---|
| B2.1 | `narrator/`: implements `Narrator`; prompt from persona style + example lines + the `NarrationRequest` (event, related events, state change, memory, recent lines, mode); may return `text=None` for colour / filler | ⬜ |
| B2.2 | `config/personas/sports_caster.yaml` and `dark_narrator.yaml` | ⬜ |
| B2.3 | Enforce the request's `max_words` (trimming); publish prompt, reply and latency | ⬜ |

**Done when:** replaying a scenario prints in-character lines for both personas.

### B3. Voice
| ID | Task | Status |
|---|---|---|
| B3.1 | `backends/kokoro.py` (`kokoro-onnx`) and `backends/piper.py` implementing `SpeechModel` | ⬜ |
| B3.2 | `voice/`: implements `Voice`; chunked playback (`sounddevice`), hard interrupt within ~200 ms with the persona interrupt sound, soft interrupt at the next sentence boundary; reports `SpeechStatus` (started with exact duration, ending, finished, interrupted) | ⬜ |

**Done when:** a critical event cuts off the current line within about 200 ms and the new line plays.

### B4. Memory
| ID | Task | Status |
|---|---|---|
| B4.1 | Fight memory: boss, attempts, deaths, moves that hit you, weapons, streaks; detect fight start / end from events | ⬜ |
| B4.2 | Session memory: capped rolling summary (fixed number of facts + counters) | ⬜ |
| B4.3 | Context builder: a few short facts for the narrator prompt; tests for the size cap | ⬜ |

**Done when:** replaying a multi-fight session gives lines that reference attempts and repeated moves.

### B5. Engine + front ends
| ID | Task | Status |
|---|---|---|
| B5.1 | `engine/`: wire the stages and queues; `start`, `stop`, `mute`, `set_persona`, `set_game` | ⬜ |
| B5.2 | Command line front end (`main.py` arguments) | ⬜ |
| B5.3 | Tray app (`pystray`) with global hotkeys (`pynput`, listen only) | ⬜ |
| B5.4 | Subtitle overlay: transparent, click-through, always-on-top window (PySide6) | ⬜ |
| B5.5 | *(Stretch)* API adapters: Claude, Gemini, ElevenLabs | ⬜ |

**Done when:** you can start, mute and switch persona from the tray during a fight, with subtitles showing.

---

## 6. Milestones (where the tracks meet)

| Milestone | Needs | What works | Status |
|---|---|---|---|
| **M0 Foundations** | Phase 0 | CI green, shared types, event bus, skeleton | ⬜ |
| **M1 First words** | A3 + B2 | A recorded boss fight (`events.jsonl` from A) replays into in-character text lines (B) | ⬜ |
| **M2 Live text** | A2, A3 + B1, B2, B5.1 | Live game → vision → director → narrator, printed as text | ⬜ |
| **M3 It talks** | M2 + B3 | Spoken commentary with interrupts | ⬜ |
| **M4 Smart** | M3 + B4 + A4 | Memory in the commentary; dev dashboard in use | ⬜ |
| **M5 v0.1.0** | M4 + A5 + B5 | Installer on GitHub Releases, tray app, overlay, demo video in the README | ⬜ |

```
Phase 0 ──┬─ A1 ─ A2 ─ A3 ─────────── A4 ─────────── A5 ──┐
(together)│                                                ├─► v0.1.0
          └─ B1 ─ B2 ──────── B3 ─ B4 ─────── B5 ──────────┘
       M0           M1    M2      M3        M4           M5
```

A1 and B1 can both start during Phase 0: the spike needs no app code, and the director only needs the shared types.

---

## 7. Working together

- **Branches:** `main` is protected. Work on `welsby/<topic>` or `nathan/<topic>`, one branch per task ID.
- **Pull requests:** small (aim for under ~400 lines), task ID in the title (e.g. `A3.4: validate vision output`), reviewed by the other person, squash-merged.
- **Ownership:** each person mostly edits their own folders (`CODEOWNERS` enforces review). `core/` changes need both approvals and the `contract` label.
- **Issues:** one GitHub issue per task ID on the Project board (To do / In progress / Done).
- **Definition of done:** tests pass, ruff clean, type hints, the stage publishes its key decisions to the event bus, and this document's status column is updated.
- **Commits:** short imperative subject line (`Add director expiry checks`).
- **Sync:** a short weekly call to demo progress and review the milestone table.

---

## 8. Decision log

| Decision | Why | Trade-offs and mitigations |
|---|---|---|
| **Vision LLM replaces OpenCV checks + rules.** One multimodal model reads the frame with the game config and previous state, and returns state + events with priorities. | One flexible stage; new games and events need YAML only; understands scenes, not just pixels. | Slower (≈1 s vs ≈1 ms) and less consistent. Mitigations: structured output with a schema from the config, validation, per-event cooldowns, previous state in the prompt, model choice from the A1 spike. If deaths feel late, a fast-path check can be added later without changing the contracts. |
| **Director uses a priority stack, not a queue.** One newest-first stack per priority level. | Commentary should be about what just happened; within the same priority the newest event is the most relevant, and older ones still reach the narrator as context. | Old events sink to the bottom of a stack; mitigated by TTL expiry on every read and a small cap per stack. Priority still always beats recency. |
| **Vision describes one screenshot; the director gathers the rest.** `VisionResult` is that frame's state, events and scene only. The director keeps the stacks, state history, what changed and the spoken lines, and reads memory facts. | Keeps the vision prompt small and fast; history lives in one place, in code, where it can be tested. | The director holds more state. Open: keep the recent state history in the director, or move it to a shared timeline in `memory/`. |
| **Prepare the next line while the current one plays.** The director asks the narrator early (lead time = measured narrator + TTS latency) and re-checks the line before speaking. | Research: parallel preparation cut silences from ~9.5 s to ~0.3 s and sounded more natural ([arXiv 2606.13322](https://arxiv.org/html/2606.13322v1)). | Some prepared lines are thrown away when something better happens; cheap with local models. |
| **Three interrupt levels with limits.** Hard (critical, cut now), soft (high, after the current sentence, only for long lower-priority lines), none. Minimum time on air, max interrupts per window. | Stops the caster cutting itself off while still reacting instantly to deaths and kills. | Some high events are spoken a second later than they could be. Limits are persona settings, so they can be tuned. |
| **Expiry from capture time; re-check against the latest state.** | Vision results are already 1–2 s old; a line can become false (e.g. after a heal) while it's being written. | Slightly more lines dropped; each drop is logged with its reason. |
| **Shorter default lines** (reaction 6, play-by-play 10, colour 18, filler 10 words: about 2–4 s). | Human commentators average about 2.8 s per line; 14-word play-by-play lines (5 s) made most high events expire while they played. | Less room for flourish; personas can raise the caps. |
| **Repeats of the event being talked about are `covered`, not queued.** | A burst of hits becomes one line instead of a pile of stale events. | The line can't mention hits that land after it was written; the next line's `counts` can. |
| **Critical lines never wait for a gap.** A second critical event follows the first as soon as it ends (no post-critical pause), without interrupting it. | Urgency beats breathing room. | Back-to-back critical lines can feel rushed; rare in practice. |
| **Memory is in RAM only** (fight + session memory). | Keep it simple; important facts live in the narrator's context. | Nothing carries over between sessions. |
| **Windows companion app, not a mod.** | Works with anti-cheat, works with any game. | Needs window capture and a screen-reading approach. |
| **Inno Setup installer that installs Ollama.** | One-click setup for non-technical users; models stay swappable. | Unsigned installer shows a SmartScreen warning. |
| **Nathan owns startup, detection and capture (A2).** | Nathan is building them now. | Track A is now vision, events, dev dashboard and packaging. |
| **Wait for the game instead of falling back to `generic.yaml`.** On launch with no known game running, show a "Waiting for game to start" overlay and attach when it starts. | Launching the app should just work, whatever order things start in. | The overlay needs a desktop (floating) mode before it docks to the game. `generic.yaml` stays available from the tray. |
| **Threads + queues between stages.** | Model calls and audio are blocking; simple to reason about. | Revisit asyncio if the dashboard or overlay needs it. |
| **Python 3.13** in a repo-local `.venv`. | Current stable Python; supported by PyInstaller; one version for both developers and CI. | Every library we add must support 3.13; check before adding one. |
