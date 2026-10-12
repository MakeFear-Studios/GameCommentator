"""The director: turns per-screenshot vision results into well-timed narration.

Everything arrives through one inbox (vision results, lines from the narrator,
speech status from the voice) and is handled on the director's own thread, which
runs `tick()` every 50 ms. All timing uses the injected clock, so tests drive
`tick()` directly with a fake one.

See docs/DESIGN.md section 4.4 for the rules this implements.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from commentator.core.config import GameConfig, PersonaConfig
from commentator.core.types import (
    CommentaryLine,
    Delivery,
    GameEvent,
    GameState,
    Mode,
    NarrationRequest,
    Narrator,
    Priority,
    SpeechState,
    SpeechStatus,
    VisionResult,
    Voice,
)
from commentator.director import policy
from commentator.director.context import build_request
from commentator.director.history import StateHistory
from commentator.director.speech import SpeechTracker
from commentator.director.stacks import PriorityStacks, StackEntry
from commentator.director.timing import LatencyTracker
from commentator.logs import EventBus, bus

STAGE = "director"
EXCLUDE_FILLER = (Priority.FILLER,)


@dataclass
class _Claim:
    """A request sent to the narrator, and the stack entries it took."""

    request: NarrationRequest
    entries: list[StackEntry] = field(default_factory=list)


@dataclass
class _Prepared:
    """A line the narrator wrote, waiting for its moment."""

    line: CommentaryLine
    claim: _Claim


class Director:
    def __init__(
        self,
        voice: Voice,
        narrator: Narrator,
        persona: PersonaConfig,
        *,
        game: GameConfig | None = None,
        memory: Callable[[], Sequence[str]] = lambda: (),
        clock: Callable[[], float] = time.monotonic,
        latency: LatencyTracker | None = None,
        event_bus: EventBus = bus,
        tick_s: float = 0.05,
    ) -> None:
        self._voice = voice
        self._narrator = narrator
        self.persona = persona
        self.chattiness = persona.chattiness
        self._cancels = {k: set(v) for k, v in (game.cancels if game else {}).items()}
        self._context_fields = game.context_fields if game else ()
        self._memory = memory
        self._clock = clock
        self._bus = event_bus
        self._tick_s = tick_s

        self.stacks = PriorityStacks(cap=self.chattiness.stack_cap)
        self.history = StateHistory()
        self.speech = SpeechTracker()
        self.latency = latency or LatencyTracker()

        self._inbox: queue.SimpleQueue[VisionResult | CommentaryLine | SpeechStatus] = queue.SimpleQueue()
        self._pending: _Claim | None = None
        self._prepared: _Prepared | None = None
        self._next_id = 0
        self._recent_lines: deque[str] = deque(maxlen=self.chattiness.recent_lines)
        self._baseline: GameState | None = None  # state when the last line was sent
        self._last_spoken: dict[Priority, float] = {}
        self._last_activity = clock()  # last time a line was sent or ended
        self._silence_announced = False
        self._colour_after: GameEvent | None = None  # critical event to reflect on after the pause

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # Inputs: safe to call from any thread

    def submit(self, result: VisionResult) -> None:
        """VisionSink: one screenshot's result from the vision stage."""
        self._inbox.put(result)

    def on_line(self, line: CommentaryLine) -> None:
        """A line written by the narrator."""
        self._inbox.put(line)

    def on_speech(self, status: SpeechStatus) -> None:
        """A status report from the voice."""
        self._inbox.put(status)

    # Thread

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="director", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._tick_s):
            self.tick()

    # The loop

    def tick(self) -> None:
        now = self._clock()
        self._drain(now)
        self._prune(now)
        self._schedule(now)

    def _drain(self, now: float) -> None:
        while True:
            try:
                item = self._inbox.get_nowait()
            except queue.Empty:
                return
            if isinstance(item, VisionResult):
                self._on_vision(item, now)
            elif isinstance(item, CommentaryLine):
                self._on_line(item, now)
            else:
                self._on_speech(item, now)

    def _prune(self, now: float) -> None:
        for entry in self.stacks.prune(now):
            self._publish("event_dropped", event=entry.event.type, reason="expired", count=entry.count)
        if self._prepared and now > self._prepared.line.expires_at:
            self._discard(self._prepared.claim, "expired", restore=False)

    def _schedule(self, now: float) -> None:
        ch = self.chattiness

        # 1. Critical events go first, interrupting anything less important.
        if self.stacks.peek(Priority.CRITICAL) and self._target_priority() is not Priority.CRITICAL:
            current = self.speech.current
            if current is None:
                self._drop_target("superseded")
                self._request(Priority.CRITICAL, Delivery.QUEUED, now)
                return
            if current.line.priority is not Priority.CRITICAL:
                self._drop_target("superseded")
                self._request(Priority.CRITICAL, Delivery.HARD_INTERRUPT, now)
                return
            # A critical line is on air: the next critical waits for it (prepared below),
            # but nothing less important may be prepared ahead of it.
            if self._target_priority() is not None:
                self._drop_target("superseded")

        # 2. Speaking: maybe soft-interrupt, otherwise prepare the next line in time.
        if self.speech.speaking:
            if self._pending or self._prepared:
                return
            top = self.stacks.top_priority(exclude=EXCLUDE_FILLER)
            if top is None or not self._cooldown_ok(top, now):
                return
            current = self.speech.current
            assert current is not None
            delivery = policy.interrupt_delivery(
                top,
                current_priority=current.line.priority,
                remaining_s=current.remaining(now),
                on_air_s=current.on_air_s(now),
                recent_interrupts=self.speech.interrupts_since(now - ch.interrupt_window_s),
                chattiness=ch,
            )
            if delivery is Delivery.SOFT_INTERRUPT:
                self._request(top, Delivery.SOFT_INTERRUPT, now)
            elif self._time_until_free(now, next_priority=top) <= self.latency.lead_time:
                self._request(top, Delivery.QUEUED, now)
            return

        # 3. Idle: speak the prepared line once the gap has passed.
        if self._prepared:
            if now >= self._speak_at() or self._prepared.line.priority is Priority.CRITICAL:
                self._speak(self._prepared, now)
            return
        if self._pending:
            return
        top = self.stacks.top_priority(exclude=EXCLUDE_FILLER)
        if top is not None and self._cooldown_ok(top, now):
            self._request(top, Delivery.QUEUED, now)  # prepared during the gap
            return
        if self._time_until_free(now) > self.latency.lead_time:
            return  # still in the gap; quiet-spell lines are asked for just in time

        # 4. Quiet: reflect on a big moment, then colour or filler after a long silence.
        if self._colour_after is not None:
            critical, self._colour_after = self._colour_after, None
            if self._memory():
                self._request(None, Delivery.QUEUED, now, extra_related=(critical,))
                return
        if now - self._last_activity < ch.max_silence_s:
            return
        if self._memory():
            self._request(None, Delivery.QUEUED, now)
        elif ch.use_filler and self.stacks.peek(Priority.FILLER) and self._cooldown_ok(Priority.FILLER, now):
            self._request(Priority.FILLER, Delivery.QUEUED, now)
        elif not self._silence_announced:
            self._silence_announced = True
            self._publish("silence_chosen", quiet_s=round(now - self._last_activity, 2))

    # Inputs

    def _on_vision(self, result: VisionResult, now: float) -> None:
        self.history.record(result)
        for event in result.events:
            if event.type in self.persona.ignore_events:
                self._publish("event_dropped", event=event.type, reason="ignored")
                continue
            if event.priority is Priority.FILLER and not self.chattiness.use_filler:
                self._publish("event_dropped", event=event.type, reason="filler_off")
                continue
            if event.type in self._types_in_progress():
                # Already being talked about (on air or being written): a burst becomes one line.
                self._publish("event_dropped", event=event.type, reason="covered")
                continue
            pushed = self.stacks.push(event)
            self._publish(
                f"event_{pushed.action}",
                event=event.type,
                priority=event.priority.value,
                count=pushed.entry.count,
                age_s=round(now - event.t, 3),
            )
            for capped in pushed.capped:
                self._publish("event_dropped", event=capped.event.type, reason="capped", count=capped.count)
            self._maybe_supersede(event, now)

    def _maybe_supersede(self, event: GameEvent, now: float) -> None:
        """A newer event can replace a line that hasn't reached the voice yet."""
        target = self._prepared.claim if self._prepared else self._pending
        if target is None or event.priority is Priority.CRITICAL:
            return  # critical is handled by the scheduler
        if event.priority.rank > target.request.priority.rank:
            self._drop_target("superseded")
        elif (
            event.priority is target.request.priority
            and target.request.delivery is Delivery.QUEUED
            and self._time_until_free(now) >= self.latency.lead_time
        ):
            self._drop_target("superseded")  # there's time to write a line about the newer event

    def _on_line(self, line: CommentaryLine, now: float) -> None:
        if self._pending is None or line.request_id != self._pending.request.id:
            self._publish("line_discarded", request=line.request_id, reason="late")
            return
        claim, self._pending = self._pending, None
        self.latency.record_narrator(now - claim.request.created_at)
        if not line.text:
            self._last_activity = now
            self._publish("silence_chosen", request=line.request_id, reason="nothing_to_say")
            return
        self._publish("line_prepared", request=line.request_id, words=len(line.text.split()))
        prepared = _Prepared(line, claim)
        if self.speech.speaking:
            now_please = line.delivery is not Delivery.QUEUED  # an interrupt: deliver straight away
        else:
            now_please = line.priority is Priority.CRITICAL or now >= self._speak_at()
        if now_please:
            self._speak(prepared, now)
        else:
            self._prepared = prepared

    def _on_speech(self, status: SpeechStatus, now: float) -> None:
        current = self.speech.current
        if (
            status.state is SpeechState.STARTED
            and current is not None
            and current.line.request_id == status.line_id
            and not current.interrupt
        ):
            self.latency.record_tts(status.t - current.sent_at)
        ended = self.speech.update(status)
        if ended is not None:
            self._last_activity = status.t
            if ended.line.priority is Priority.CRITICAL and ended.line.event is not None:
                self._colour_after = ended.line.event
            self._publish("line_ended", request=status.line_id, state=status.state.value)

    # Requests and lines

    def _request(
        self,
        priority: Priority | None,
        delivery: Delivery,
        now: float,
        *,
        extra_related: Sequence[GameEvent] = (),
    ) -> None:
        chosen, rest = self.stacks.take(priority) if priority is not None else (None, [])
        mode = policy.mode_for(priority) if priority is not None else Mode.COLOUR
        backlog = self.stacks.top_priority(exclude=EXCLUDE_FILLER) is not None  # more still waiting
        self._next_id += 1
        request = build_request(
            self._next_id,
            now=now,
            chosen=chosen,
            rest=rest,
            mode=mode,
            delivery=delivery,
            history=self.history,
            baseline=self._baseline,
            memory=self._memory(),
            recent_lines=list(self._recent_lines),
            chattiness=self.chattiness,
            backlog=backlog,
            extra_related=extra_related,
        )
        self._pending = _Claim(request, ([chosen] if chosen else []) + list(rest))
        self._publish(
            "event_chosen",
            request=request.id,
            event=chosen.event.type if chosen else None,
            related=[e.type for e in request.related],
            priority=request.priority.value,
            mode=mode.value,
            delivery=delivery.value,
            max_words=request.max_words,
        )
        self._narrator.request(request)

    def _speak(self, prepared: _Prepared, now: float) -> None:
        if self._prepared is prepared:
            self._prepared = None
        reason = self._recheck(prepared, now)
        if reason is not None:
            # Only a superseded line's events are still worth talking about later.
            self._discard(prepared.claim, reason, restore=reason == "superseded")
            return
        line = prepared.line
        speaking = self.speech.speaking
        if speaking and line.delivery is Delivery.HARD_INTERRUPT:
            self._voice.interrupt(line)
            self._publish("interrupt", request=line.request_id, kind="hard")
        elif speaking and line.delivery is Delivery.SOFT_INTERRUPT:
            self._voice.play(line)
            self._publish("interrupt", request=line.request_id, kind="soft")
        else:
            line = replace(line, delivery=Delivery.QUEUED)
            self._voice.play(line)
        counts_as_interrupt = speaking and line.priority is not Priority.CRITICAL
        self.speech.sent(line, now, policy.estimate_duration(line.text, self.chattiness), interrupt=counts_as_interrupt)
        assert line.text is not None
        self._recent_lines.append(line.text)
        self._baseline = self.history.latest
        self._last_spoken[line.priority] = now
        self._last_activity = now
        self._silence_announced = False
        self._colour_after = None
        self._publish(
            "line_sent",
            request=line.request_id,
            event=line.event.type if line.event else None,
            priority=line.priority.value,
            mode=line.mode.value,
            delivery=line.delivery.value,
            text=line.text,
        )

    def _recheck(self, prepared: _Prepared, now: float) -> str | None:
        """Last check before speaking: still in time, still the most important, still true."""
        line, claim = prepared.line, prepared.claim
        if now > line.expires_at:
            return "expired"
        if self.stacks.has_above(line.priority):
            return "superseded"
        if self._is_stale(claim):
            return "stale_state"
        return None

    def _is_stale(self, claim: _Claim) -> bool:
        if claim.entries:
            types = {e.event.type for e in claim.entries}
            newest = max(e.event.t for e in claim.entries)
            for cancelling, cancelled in self._cancels.items():
                if cancelled & types and self.history.seen_since({cancelling}, newest + 1e-9):
                    return True
        latest = self.history.latest
        if latest is not None:
            before = claim.request.state.fields
            if any(before.get(f) != latest.fields.get(f) for f in self._context_fields):
                return True
        return False

    def _discard(self, claim: _Claim, reason: str, *, restore: bool = True) -> None:
        if self._pending is claim:
            self._pending = None
        if self._prepared is not None and self._prepared.claim is claim:
            self._prepared = None
        if restore:
            self.stacks.restore(claim.entries)
        self._publish(
            "line_discarded",
            request=claim.request.id,
            event=claim.request.event.type if claim.request.event else None,
            reason=reason,
        )

    def _drop_target(self, reason: str) -> None:
        if self._prepared is not None:
            self._discard(self._prepared.claim, reason)
        if self._pending is not None:
            self._discard(self._pending, reason)

    # Helpers

    def _target_priority(self) -> Priority | None:
        if self._prepared is not None:
            return self._prepared.line.priority
        if self._pending is not None:
            return self._pending.request.priority
        return None

    def _types_in_progress(self) -> set[str]:
        """Event types of the line on air and the line being prepared."""
        types: set[str] = set()
        if self.speech.current is not None and self.speech.current.line.event is not None:
            types.add(self.speech.current.line.event.type)
        target = self._prepared.claim if self._prepared else self._pending
        if target is not None and target.request.event is not None:
            types.add(target.request.event.type)
        return types

    def _ready_at(self) -> float:
        """When the voice may start the next line (end of the last line plus the gap)."""
        if self.speech.last_ended_at is None:
            return float("-inf")
        return self.speech.last_ended_at + policy.gap_after(self.speech.last_priority, self.chattiness)

    def _speak_at(self) -> float:
        """When to hand the next line to the voice, so its audio starts as the gap ends."""
        return self._ready_at() - self.latency.tts_s

    def _time_until_free(self, now: float, next_priority: Priority | None = None) -> float:
        """Time until the next line's audio may start. Critical lines never wait for a gap."""
        current = self.speech.current
        if current is not None:
            gap = (
                0.0 if next_priority is Priority.CRITICAL else policy.gap_after(current.line.priority, self.chattiness)
            )
            return current.remaining(now) + gap
        return max(0.0, self._ready_at() - now)

    def _cooldown_ok(self, priority: Priority, now: float) -> bool:
        last = self._last_spoken.get(priority)
        return last is None or now - last >= self.chattiness.cooldown_s.get(priority, 0.0)

    def _publish(self, type: str, **data: Any) -> None:
        self._bus.publish(STAGE, type, data)
