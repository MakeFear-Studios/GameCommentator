# `commentator/logs`: log bus, dev logger, log reader

Task **0.4** in [`docs/IMPLEMENTATION.md`](../../docs/IMPLEMENTATION.md). Owner: Welsby (Track A).

This package is the **one place every stage reports what it's doing**. In dev mode (`--dev`) everything published here is written to `logs/<session>/` and can be streamed to the dev dashboard or replayed later.

Read this file before publishing from a new stage, adding a subscriber, or changing the log format.

---

## TL;DR for a stage author

```python
from commentator.logs import bus

bus.publish("director", "event_dropped", {"event": "healed", "reason": "expired"})
bus.publish("vision", "model_timeout", {"model": "qwen2.5vl:3b", "after_s": 5}, level="warning")
```

- `stage`: your package name (`"capture"`, `"vision"`, `"director"`, `"narrator"`, `"voice"`, `"memory"`, ...).
- `type`: what happened, **snake_case past tense** (`frame_captured`, `event_pushed`, `line_spoken`).
- `data`: a small, flat, JSON-friendly dict. **No images or large blobs**; see [Frames](#frames).
- `level`: `"debug" | "info" (default) | "warning" | "error"`.

Publish **decisions and timings**, not every loop iteration.

---

## Ground rules

1. **Observation only.** Stages pass real data to each other through their own `queue.Queue`s. Nothing in the pipeline may depend on the bus or read from it to make decisions. With no subscribers, the app must behave identically.
2. **`publish` never blocks and never raises because of logging.** It appends to an in-memory buffer and returns. (It *does* raise `ValueError` for an invalid `level`, which is a programming error.)
3. **Free when off.** With no subscribers, `publish` returns after one check, so call it freely.
4. **Never stall the game.** If subscribers fall behind and the buffer fills, the **oldest** events are dropped and counted.
5. **`commentator/events/` is something else (TBD).** Logging lives here only.

---

## Files

| File | Contents |
|---|---|
| `__init__.py` | Public API and the app-wide `bus = EventBus()` |
| `bus.py` | `LogEvent`, `EventBus`, `LEVELS` |
| `dev_logger.py` | `DevLogger`, `FORMAT_VERSION` |
| `reader.py` | `read_log()` |
| `python_logging.py` | `BusLogHandler`, `capture_python_logging()` |
| `serialize.py` | `to_jsonable()`, `dumps()`: crash-proof JSON encoding |

Tests: [`tests/logs/`](../../tests/logs/). Standard library only at runtime (numpy is only used in tests).

---

## `LogEvent` (`bus.py`)

```python
@dataclass(frozen=True)
class LogEvent:
    seq: int            # strictly increasing; exact order even when timestamps tie
    t: float            # seconds since the bus was created (time.monotonic, never jumps)
    stage: str
    type: str
    data: Mapping[str, Any] = {}
    level: str = "info"

    def to_dict(self) -> dict   # the on-disk shape; t rounded to microseconds
```

Named `LogEvent` (not `Event`) so it's never confused with `GameEvent`, the things the director narrates.

## `EventBus` (`bus.py`)

| Method | Behaviour |
|---|---|
| `publish(stage, type, data=None, level="info")` | Non-blocking. No-op if there are no subscribers or the bus is closed. |
| `subscribe(callback)` | `callback(LogEvent)` is called **on the bus thread** for every event. Starts the bus thread on first use. Subscribing the same callback twice has no effect. |
| `unsubscribe(callback)` | Compares with `==`, so passing `obj.method` again works (bound methods are new objects on every access). |
| `flush(timeout=5.0) -> bool` | Blocks until everything buffered has been delivered. Returns `False` on timeout. Use in tests and before shutdown. |
| `close(timeout=5.0)` | Flushes, stops the bus thread, ignores later publishes. Irreversible. |
| `now() -> float` | Seconds since the bus was created, on the same clock as `LogEvent.t`. |
| `dropped` | Total events dropped because the buffer was full. |

**How delivery works**
```
any thread ── publish() ──► [ bounded deque, default 10,000 ] ──► bus thread ──► subscriber 1, 2, …
                              (oldest dropped when full)            (in order, one batch at a time)
```
- One `threading.Condition` guards the buffer; `seq` and `t` are assigned under it, so order is exact across threads.
- The bus thread is a **daemon** thread named `log-bus`. It drains the whole buffer as a batch, then calls each subscriber for each event, in order.
- **Drops are reported:** after a drop, the next batch ends with a synthetic event `stage="logs", type="events_dropped", level="warning", data={"count": n, "total": N}`.
- **A failing subscriber** can't break others or the app: its exception is printed to stderr **once** (later failures from the same subscriber are silent) and delivery continues. Failures are deliberately **not** re-published to the bus, because a subscriber that always fails would loop forever.
- Subscriber callbacks run on the bus thread: keep them quick, and don't call `bus.flush()` or `bus.close()` from inside one. The bus would be waiting on itself: `flush()` hangs until its timeout, and `close()` fails trying to join its own thread.

**The app-wide bus.** `commentator.logs.bus` is created at import. For tests, create your own `EventBus()` and `close()` it afterwards.

---

## `DevLogger` (`dev_logger.py`)

A subscriber that writes a session folder.

```python
from commentator.logs import bus, DevLogger

with DevLogger(bus, root="logs", session_info={"game": "elden_ring", "persona": "dark_narrator"}) as logger:
    ...                      # run the app; everything published lands in logger.session_dir
```

or `logger.start()` / `logger.stop()`.

**Session folder**
```
logs/2026-10-12_15-30-04/        # local start time; "_2", "_3", … if the name is taken
├── events.jsonl                 # one LogEvent per line
├── session.json                 # metadata, rewritten on stop
└── frames/                      # PNGs saved with save_frame()
```

**`start()`:** creates the folder and `frames/`, writes `session.json` (with `ended_at: null`), opens `events.jsonl` **line-buffered** (every event reaches the OS as soon as it's written, so a crash keeps the log), subscribes, then publishes `logs / session_started` with `{"format_version": 1, "session": "<folder name>", **session_info}`.

**`stop()`:** publishes `logs / session_ended` with `{"dropped": n}`, flushes the bus, unsubscribes, closes the file, and rewrites `session.json` with `ended_at`, `events_written` and `events_dropped`. Safe to call twice.

**`session.json`**
```json
{
  "format_version": 1,
  "session": "2026-10-12_15-30-04",
  "started_at": "2026-10-12T15:30:04.123456+01:00",
  "ended_at": "2026-10-12T15:42:10.987654+01:00",
  "events_written": 5123,
  "events_dropped": 0,
  "python": "3.12.5",
  "platform": "Windows-11-…",
  "app": { "game": "elden_ring", "persona": "dark_narrator" }
}
```
`started_at` (wall clock) plus each event's `t` (seconds since start) gives the real time of any event.

### Frames

Never put images in `data`. Encode the PNG yourself, save it, and reference it by path:

```python
rel = logger.save_frame(png_bytes)            # → "frames/000001.png" (or save_frame(png, name="boss_intro.png"))
bus.publish("capture", "frame_sampled", {"frame": rel})
```

Sampling (how often to save) is the caller's decision (capture, task A2.4), not the logger's.

---

## Log line format (`events.jsonl`)

```json
{"seq": 42, "t": 12.43, "stage": "director", "type": "event_dropped", "level": "info", "data": {"event": "healed", "reason": "expired"}}
```

- Keys are always exactly `seq, t, stage, type, level, data`.
- First event: `logs / session_started` (carries `format_version`). Last: `logs / session_ended`.
- **Bump `FORMAT_VERSION`** in `dev_logger.py` if you change this shape, and keep `read_log()` able to read older versions.

### Encoding awkward values (`serialize.py`)

`json.dumps(..., default=to_jsonable)` so the logger never crashes on data:

| Value | Written as |
|---|---|
| `Enum` (e.g. `Priority.HIGH`) | its value: `"high"` |
| dataclass (e.g. a `GameEvent`) | `dataclasses.asdict(...)` |
| `datetime` / `date` | ISO 8601 string |
| `Path` | string |
| `set` / `frozenset` | sorted list |
| `bytes` | `{"bytes": <length>}` |
| numpy scalar | plain number |
| numpy array (anything with `shape` + `dtype`) | `{"array": {"shape": [...], "dtype": "..."}}`; **never the pixels** |
| anything else | `str(value)` |

---

## `read_log()` (`reader.py`)

```python
from commentator.logs import read_log

for event in read_log("logs/2026-10-12_15-30-04"):      # folder or events.jsonl
    ...                                                  # LogEvent objects, oldest first
```

- Skips blank lines.
- Skips invalid lines (e.g. a half-written last line after a crash); `strict=True` raises `ValueError("<path>:<line>: invalid log line")` instead.
- Missing `level` defaults to `"info"`, missing `data` to `{}`.

Used by: `ReplayEventSource` (A3.6), the dev dashboard's replay mode (A4.2), and tests.

---

## Python `logging` bridge (`python_logging.py`)

```python
import logging
from commentator.logs import bus, capture_python_logging

handler = capture_python_logging(bus, level=logging.WARNING)   # attach to the root logger
...
logging.getLogger().removeHandler(handler)                     # detach
```

- Each record becomes `stage="log", type="log_record", data={"logger": name, "message": text}`, plus `"exception"` (formatted traceback) when `exc_info` is set.
- Levels map `DEBUG→debug, INFO→info, WARNING→warning, ERROR→error, CRITICAL→error`.
- Lowers the root logger's level to `level` if it was higher, so the records actually reach the handler.
- Our own code should publish to the bus directly; this bridge is for **third-party libraries** (model clients, capture libraries).

---

## Testing

```bash
python -m pytest tests/logs -q
```

What's covered: ordering and `seq`; non-decreasing `t`; 8 threads × 500 events with nothing lost; buffer overflow drops the oldest and emits `events_dropped`; a failing subscriber is reported once and others still receive; `unsubscribe` with a fresh bound method; invalid level; `close()` drains then ignores; session folder contents and `session.json`; events on disk before `stop()`; `stop()` really unsubscribes; unique folders within the same second; awkward data encoding; `save_frame`; `read_log` round-trip, bad lines, strict mode and defaults; the `logging` bridge (levels, threshold, tracebacks).

Patterns for testing your own stage:
```python
def test_my_stage_reports_drops():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    run_stage(bus=bus)            # inject the bus rather than using the global one
    bus.flush()
    assert any(e.type == "event_dropped" for e in seen)
    bus.close()
```

---

## Status and next steps

- ✅ Bus, dev logger, reader, `logging` bridge, encoding, 24 tests.
- ⬜ **Hook up `--dev` in `main.py`:** start a `DevLogger` and `capture_python_logging` when `--dev` is passed, stop on exit. Depends on task 0.5 (`main.py` arguments).
- ⬜ Live streaming to the dev dashboard (A4.1) will be another subscriber (e.g. pushing to a WebSocket); nothing here needs to change.
- Possible later: a `--log-level` option to drop `debug` events at publish time; per-stage counters in `session.json`.
