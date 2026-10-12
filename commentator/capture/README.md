# `commentator/capture`: the game window's newest frame

Task **A2.2 – A2.4** in [`docs/IMPLEMENTATION.md`](../../docs/IMPLEMENTATION.md). Owner: Nathan. Design: [`docs/DESIGN.md` § 4.2](../../docs/DESIGN.md#42-window-capture).

This package captures **one window** (the game, found by `detection/`), wherever it is and even if partly covered, keeps only the **newest frame**, pauses while the game isn't being played, and hands each fresh frame to the next stage (vision) through the `FrameSink` contract.

Read this before changing backends, the pause rules, or what gets sent to vision.

---

## Using it

```python
from commentator.capture.manager import CaptureManager

capture = CaptureManager(
    hwnd,                         # from detection's Match.window.hwnd
    sink=vision_stage,            # anything with submit(Frame); NullFrameSink until vision exists
    save_frame=dev_logger.save_frame,  # dev mode only: sample PNGs into the session folder
)
capture.start()                   # picks a backend; returns once it has a frame (or gave up)
frame = capture.latest()          # newest Frame, or None while paused / just resumed
capture.stop()
```

`Frame` (in `core/types.py`): `image` (height × width × 4, **BGRA**, uint8) and `t` (`time.monotonic()` when captured). Vision results and events carry this `t` forward, so the director measures expiry from capture time.

---

## Backends

| Backend | File | How | Role |
|---|---|---|---|
| Windows Graphics Capture | `wgc.py` | `windows-capture` by window handle, yellow border off, **max 10 fps** | Default |
| Screen grab | `mss_backend.py` | `mss` of the window's client area, 10 fps | Fallback |

- `CaptureManager` tries them in order and keeps the first that **delivers a frame within 2 s**. If one fails to start or sends nothing, it's stopped and the next is tried (`backend_failed`). If none work: `capture_failed`.
- A minimised window sends no frames, so it doesn't count against a backend.
- WGC only sends frames when the picture changes; a static screen means few frames, not a failure.
- The 10 fps cap matters: copying every 2560×1600 frame at 60 fps wastes CPU, and vision reads about one a second.
- `mss` sees whatever is on top of the game, which is why it's only the fallback.
- Both copy each frame out of the library's buffer (WGC reuses its memory).

All backends implement `CaptureBackend` (`base.py`): `start(hwnd)`, `stop()`, `latest()`, `frame_count`. `LatestFrame` is the thread-safe single-slot buffer: writers overwrite, readers never fall behind.

---

## Pausing and fresh frames (`manager.py`)

Every **0.25 s** the tracking thread checks the window:

| Window | Capture |
|---|---|
| Focused, not minimised | Running |
| Minimised or another window focused | **Paused** (`capture_paused`, reason `minimized` / `unfocused`) |
| Gone | Nothing; detection notices and stops capture |

Guarantees for whatever reads frames:
- `latest()` returns **None while paused**.
- After a resume, `latest()` stays **None until a frame captured after the resume arrives**, so nobody sees the picture from before the pause.
- While paused, no stats, no saved frames, nothing sent to the sink.
- `capture_idle` after 2 s of no new frames while focused (a stall or just a static screen); `capture_active` when they return.

## Sending frames on (`FrameSink`)

On each tracking step, if there's a new fresh frame, it's passed to `sink.submit(frame)` **once** (so up to 4 per second). The sink must not block: the vision stage should keep only the newest and analyse it when the model is free. An exception from `submit` is published as `sink_error` and capture carries on.

---

## Published events (`stage="capture"`)

| Type | Data | When |
|---|---|---|
| `capture_started` | `backend` | A backend delivered its first frame |
| `backend_failed` | `backend`, `error` | Couldn't start, or no frames within 2 s |
| `capture_failed` | `hwnd` | No backend worked |
| `capture_paused` / `capture_resumed` | `reason` / – | Game minimised or unfocused / back |
| `capture_idle` / `capture_active` | `seconds` / – | Frames stopped / started arriving while focused |
| `frame_captured` | `backend`, `fps`, `frames`, `sent`, `frame_size`, `window`, `paused` | Every 5 s while running (stats, not every frame) |
| `frame_saved` | `frame` (path in the session folder) | Dev mode, every 5 s |
| `sink_error` | `error` | The next stage raised |
| `capture_stopped` | `backend` | Stopped |

---

## Testing

```bash
python -m pytest tests/test_capture.py -q
```

Covered with fake backends and a fake clock: the newest-frame buffer (empty, overwrite, thread safety); backend choice and fallback (no frames, exception, none working); pause on unfocus and minimise, one event per change; no stale frame after resume; no stats or saved frames while paused; fps restarts after a resume; idle and active; stats and PNG saving; each new frame sent to the sink once; nothing sent while paused or stale; a broken sink doesn't stop capture.

Not unit-tested: the real `windows-capture` and `mss` calls. Checked by hand with PICO PARK (about 9 fps at 2560×1600, minimise and alt-tab).

---

## Status and next steps

- ✅ WGC + mss behind one interface, newest-frame buffer, pause and fresh-frame rules, `FrameSink` hand-off, dev-mode frames.
- ⬜ Check the `mss` fallback on a real window, and capture in Elden Ring.
- ⬜ The vision stage (A3) implements `FrameSink` and replaces `NullFrameSink` in `commentator/app.py`.
- Possible later: downscale before sending (vision wants about 768 px wide) to save copying, if profiling shows it matters.
