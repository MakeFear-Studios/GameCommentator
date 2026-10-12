# `commentator/director`: what to talk about, and when

Task **B1** in [`docs/IMPLEMENTATION.md`](../../docs/IMPLEMENTATION.md). Owner: Nathan (Track B). Design and research: [`docs/DESIGN.md` § 4.4](../../docs/DESIGN.md#44-director-when-to-speak-and-what-about).

The director turns per-screenshot vision results into **well-timed narration requests**. Vision describes one screenshot at a time; the director keeps everything about the past (waiting events, state history, lines already spoken), reads memory, and owns **all timing**: which event, when to ask the narrator, when to speak, when to interrupt, and when to stay quiet. The narrator only writes lines; the voice only speaks them.

The goal is commentary that sounds fluent: no long gaps, no talking over itself, few self-interruptions, and nothing stale or untrue.

Read this before changing timing rules, persona `chattiness`, or the contracts with vision, the narrator or the voice.

---

## Wiring it up

```python
from commentator.director import Director

director = Director(
    voice,                  # Voice: play(line), interrupt(line)
    narrator,               # Narrator: request(req), must not block
    persona,                # PersonaConfig (core/config.py): chattiness, ignored events
    game=game_config,       # optional: cancels / context_fields for the re-check
    memory=lambda: facts,   # optional: () -> short facts for colour lines (B4)
)
narrator.reply = director.on_line     # the narrator calls this with each CommentaryLine
voice.report = director.on_speech     # the voice calls this with each SpeechStatus
director.start()                      # own thread, 50 ms tick
vision.sink = director                # Director implements VisionSink: submit(VisionResult)
...
director.stop()
```

`submit`, `on_line` and `on_speech` only put items in the director's inbox, so they're safe from any thread and never block. Everything else happens on the director thread.

For tests, pass `clock=` and `event_bus=` and call `tick()` yourself (or use the simulation, below).

---

## The contracts (`core/types.py`)

**In:** `VisionResult(state, events, frame_t, latency_ms)`: one screenshot. Each `GameEvent` arrives with its `priority` and `ttl` already set by the vision stage; the director never re-ranks events. `GameEvent.t` is the **capture time**, so expiry (`t + ttl`) counts from the screenshot, not arrival.

**Out:** `NarrationRequest`: the chosen event (or none, for a colour line), related events, merged `counts`, latest `state`, `state_change` since the last spoken line, `memory`, `recent_lines`, `mode`, `priority`, `max_words`, `delivery`, `created_at`, `expires_at`.

**Back:** `CommentaryLine` from the narrator (`text=None` means "nothing to say"), `SpeechStatus` from the voice (`started` with the exact duration, `ending` with time left, `finished`, `interrupted`).

### What the voice must do

| Call | Line's `delivery` | Voice does |
|---|---|---|
| `play(line)` | `queued` | The director only calls this when the voice is free: start the line |
| `play(line)` | `soft_interrupt` | Finish the current **sentence**, then start the line |
| `interrupt(line)` | `hard_interrupt` | Stop at the next audio chunk (~200 ms), play the persona's interrupt sound, then the line |

It must report `SpeechStatus` for every line, with the real audio length on `started`. The director times everything from those reports.

### What the narrator must do

Answer every request exactly once, via `on_line`, with the request's `id`. Keep to `max_words`. It may return `text=None` for `colour` and `filler`. Replies to requests the director has since dropped are ignored (`line_discarded`, reason `late`).

---

## How one tick works (`director.py`)

1. **Drain the inbox.** Vision results go into history and the stacks; lines and speech status update state.
2. **Prune.** Drop expired events (`event_dropped`, `expired`) and an expired prepared line.
3. **Critical first.** If a critical event waits and nothing critical is being prepared:
   - voice idle → ask now (`queued`; it skips the gap when it arrives);
   - a less important line on air → ask now with `hard_interrupt`;
   - a critical line on air → wait for it (no critical-on-critical interrupts), but drop any less important line prepared ahead of it.
4. **Speaking.** If nothing is being prepared, take the highest non-filler level (respecting cooldowns):
   - soft interrupt if the policy allows (below) → ask with `soft_interrupt`;
   - otherwise, once *time until the voice is free* ≤ **lead time**, ask with `queued`, so the line is ready as the voice finishes.
5. **Idle.** Speak the prepared line once its moment comes; otherwise ask for the next event straight away (it's written during the gap).
6. **Quiet.** After a critical line and its pause, a `colour` reflection (if memory has facts). After `max_silence_s` with nothing to say: a `colour` line from memory, else a `filler` line (if the persona uses filler), else `silence_chosen` (once per quiet spell).

### Timing

- **Lead time** = running average of narrator latency + TTS start-up + 0.2 s margin (`timing.py`, EMA, starts at 0.8 + 0.3). It adapts to slow or fast models.
- **Time until free** = remaining speech (from the voice's reports) + the gap after it (`min_gap_s`, or `after_critical_pause_s` after a critical line; **0 if the next line is critical**).
- **Handing over:** a queued line goes to the voice at *(end of last line + gap − TTS start-up)*, so the audio starts as the gap ends. Critical lines skip the gap.
- The shortest real gap is the TTS start-up time: a queued line is never handed over while the voice is still speaking (that would overlap).

### Stacks (`stacks.py`)

- One stack per priority, ordered by capture time, newest on top. The director takes **the whole highest level**: the top is the chosen event, the rest go along as `related` context.
- Same type already waiting → **merged** (newest kept, count summed, `event_merged`).
- Same type as the line **on air or being written** → dropped as **`covered`** (a burst becomes one line).
- Each level keeps `stack_cap` entries; the oldest are dropped (`capped`).

### Interrupts (`policy.py`)

Only a **strictly more important** event can interrupt.

| Event | Interrupts | When |
|---|---|---|
| `critical` | Hard | Any lower line, immediately; not limited by the budget |
| `high` | Soft | A `normal` or `filler` line with more than `soft_min_remaining_s` left, on air at least `min_on_air_s`, and fewer than `max_in_window` non-critical interrupts in the last `window_s` |
| `normal`, `filler` | Never | Wait for the voice |

### Replacing a line before it's spoken

A line that's prepared (or still being written) but not yet handed to the voice is dropped (`line_discarded`, `superseded`) and its events go **back on the stack** when:
- a more important event arrives, or
- a newer event of the **same** priority arrives and there's still at least the lead time before the line would be spoken (time to write a better one).

### Re-check before speaking

Every line is checked again just before it goes to the voice:

| Reason | Check | Events go back? |
|---|---|---|
| `expired` | now > `expires_at` (the chosen event's capture time + ttl) | No |
| `superseded` | something more important is waiting | Yes |
| `stale_state` | a `cancels` event was seen after the line's events (e.g. `healed` cancels `low_hp`), or a `context_fields` value changed (e.g. `boss_name`) | No (they're untrue now) |

### Modes and length

| Mode | For | Default `max_words` |
|---|---|---|
| `reaction` | critical | 6 |
| `play_by_play` | high, normal | 10 |
| `colour` | quiet spells, after big moments (from memory) | 18 |
| `filler` | scene remarks in quiet spells | 10 |

While other non-filler events are still waiting (a backlog), lines other than reactions are capped at `words_per_second × backlog_line_s` to catch up. Defaults are about 2–4 s of speech; human commentators average about 2.8 s per line.

---

## Persona settings (`chattiness:` in `config/personas/*.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `min_gap_s` | 1.5 | Silence between lines |
| `max_silence_s` | 6.0 | Quiet this long before a colour or filler line |
| `words_per_second` | 2.8 | TTS speaking rate, to turn time into words |
| `max_words` | `{reaction: 6, play_by_play: 10, colour: 18, filler: 10}` | Line length per mode |
| `cooldown_s` | `{normal: 3, filler: 15}` (others 0) | Minimum time since the last line of the same priority |
| `interrupts.min_on_air_s` | 0.8 | Before anything but critical can interrupt |
| `interrupts.window_s` / `max_in_window` | 4.0 / 1 | Non-critical interrupt budget |
| `interrupts.soft_min_remaining_s` | 1.5 | Only soft-interrupt lines with more than this left |
| `after_critical_pause_s` | 2.0 | Deliberate silence after a critical line |
| `backlog_line_s` | 3.0 | Line length while events are backed up |
| `stack_cap` | 5 | Entries per priority level |
| `recent_lines` | 5 | Lines passed back to the narrator to avoid repeats |
| `use_filler` | true | Whether filler events are used at all (`filler_off` if not) |

Also used: `event_overrides: {type: {ignore: true}}` → those events are dropped as `ignored` (the dark narrator ignores `healed`).

## Game config keys (`config/games/*.yaml`, optional)

```yaml
events:
  healed: { priority: normal, ttl: 3.0, cancels: [low_hp, out_of_flasks] }
context_fields: [boss_name]
```

---

## Published events (`stage="director"`)

| Type | Data | When |
|---|---|---|
| `event_pushed` / `event_merged` | `event`, `priority`, `count`, `age_s` | An event joined a stack |
| `event_dropped` | `event`, `reason` (`expired`, `capped`, `covered`, `ignored`, `filler_off`), `count` | An event won't be talked about |
| `event_chosen` | `request`, `event`, `related`, `priority`, `mode`, `delivery`, `max_words` | A request was sent to the narrator |
| `line_prepared` | `request`, `words` | The narrator's line arrived |
| `line_discarded` | `request`, `event`, `reason` (`superseded`, `expired`, `stale_state`, `late`) | A line won't be spoken |
| `interrupt` | `request`, `kind` (`hard`, `soft`) | A line cut into another |
| `line_sent` | `request`, `event`, `priority`, `mode`, `delivery`, `text` | Handed to the voice |
| `line_ended` | `request`, `state` (`finished`, `interrupted`) | The voice finished or was cut off |
| `silence_chosen` | `quiet_s`, or `request` + `reason: nothing_to_say` | Chose to stay quiet |

---

## Files

| File | What |
|---|---|
| `director.py` | `Director`: inbox, tick, scheduling, re-checks |
| `stacks.py` | `PriorityStacks`, `StackEntry` |
| `history.py` | `StateHistory`: recent states, events seen, `change_since` |
| `speech.py` | `SpeechTracker`: line on air, remaining time, interrupt history |
| `timing.py` | `LatencyTracker`: narrator / TTS averages, lead time |
| `policy.py` | Pure functions: `interrupt_delivery`, `mode_for`, `max_words`, `gap_after`, `estimate_duration` |
| `context.py` | `build_request` |
| `sim.py` | Simulation harness: fakes, scenarios, `Simulation` |
| `fluency.py` | `fluency_report`, `FluencyReport`, `SpokenLine` |

---

## Simulation and the fluency report

`sim.py` runs the director in **simulated time** with a fake vision source, narrator and voice, so a minute of commentary takes milliseconds and needs no game or models:

```python
from commentator.core.config import load_persona
from commentator.director.sim import Simulation, scenarios, frame, event

sim = Simulation(scenarios.boss_fight_ending_in_death(), load_persona(path),
                 vision_latency_s=1.0, narrator_latency_s=0.6, tts_latency_s=0.3)
sim.run(until=35)
print(sim.spoken_texts())
print(sim.report())   # FluencyReport
```

- `frame(t, *events, **state_fields)` and `event(type, priority, t, ttl=...)` build scripted results.
- Scenarios: `boss_fight_ending_in_death`, `burst_of_hits`, `quiet_exploration`, `heal_cancels_low_hp`.
- `FakeVoice` behaves like the real one should (TTS start-up, durations from word count, hard and soft interrupts) and **overlaps** if told to play while busy, so the report catches director mistakes.
- `FluencyReport`: `overlap_pct`, `mean_gap_s` (gaps under 10 s; longer ones are quiet spells), `longest_silence_s`, soft / hard interrupts, `non_critical_interrupts_per_min`, `dropped` and `discarded` reasons. `meets_targets(min_gap_s)` checks **0% overlap, mean gap under 2 s (or the persona's `min_gap_s` + 0.5 s), at most 2 non-critical interrupts per minute**.

Results with the repo personas (vision 1 s, narrator 0.6 s, TTS 0.3 s): no overlap in any scenario; in the boss fight the mean gap is 1.8 s for the sports caster (`min_gap_s` 1.5) and 2.7 s for the dark narrator (`min_gap_s` 4, shorter on average because critical lines skip the gap); `you_died` is heard 2.0 s after its screenshot; a burst of five hits gives one line.

---

## Testing

```bash
python -m pytest tests/director -q
```

`tests/director/` (83 tests, 100% line coverage of `director.py`):
- `test_stacks.py`: newest first, priority beats recency, merging, cap, expiry from capture time, ordering, restore.
- `test_parts.py`: history and `change_since`, speech tracking (estimate → real duration → ending hint), interrupt window, lead-time averages, every policy rule.
- `test_director.py`: request contents and context, state change since the last line, expiry, ignored and filler-off events, covered repeats, backlog length, gaps, preparing early, adaptive lead time, cooldowns, hard / soft interrupts and their limits, critical after critical, superseding (by priority, by a newer event when there's time, in the gap), late replies, expiring while written, stale lines (`cancels`, `context_fields`) not retried, quiet spells (silence, colour, filler, nothing to say), pause and reflection after big moments, the real thread.
- `test_scenarios.py`: every scenario × every persona meets the fluency targets; critical latency; the story of a boss fight; bursts; quiet exploration; heal cancelling low HP; the dark narrator ignoring heals; colour lines; the fluency report itself; the fake voice.

Pattern for a new rule: build a scenario with `frame`/`event`, run a `Simulation` with `vision_latency_s=0` for exact timings, and assert on `sim.narrator.requests`, `sim.voice.spoken()` and `sim.bus.of_type(...)`.

---

## Status and next steps

- ✅ B1.1–B1.7: stacks, history, speech tracking, adaptive timing, interrupts, re-checks, quiet spells, published decisions, simulation and fluency report.
- 🟨 B1.0: the contract types are in `core/types.py`; they need Welsby's approval (`contract` label).
- ⬜ Not wired into the app yet: needs a real `Narrator` (B2) and `Voice` (B3) that follow the rules above, and the vision stage (A3) calling `submit`.
- ⬜ Memory (B4) to feed `memory=` (colour lines only happen when it has facts).
- ⬜ Tune the persona settings by ear once real TTS is in, and check the fluency report on recorded sessions (replay through `ReplayVisionSource`).
- Open: keep the recent state history here, or move it to a shared timeline in `memory/` ([DESIGN.md § 10](../../docs/DESIGN.md#10-open-questions)).
