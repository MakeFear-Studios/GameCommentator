"""Run the director without the game: fake clock, vision, narrator and voice.

Used by the tests and for tuning timing. Everything runs on one thread in fixed
steps of simulated time, so a minute of commentary takes milliseconds.

    sim = Simulation(scenarios.boss_fight_ending_in_death(), persona)
    sim.run(until=40)
    print(sim.report())
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from commentator.core.config import GameConfig, PersonaConfig
from commentator.core.types import (
    CommentaryLine,
    Delivery,
    GameEvent,
    GameState,
    Mode,
    NarrationRequest,
    Priority,
    SpeechState,
    SpeechStatus,
    VisionResult,
)
from commentator.director.director import Director
from commentator.director.fluency import FluencyReport, SpokenLine, fluency_report


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class ListBus:
    """Synchronous stand-in for the log bus: keeps every event as a dict."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, stage: str, type: str, data: Mapping[str, Any] | None = None, level: str = "info") -> None:
        self.events.append({"stage": stage, "type": type, "data": dict(data or {}), "level": level})

    def of_type(self, type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type]


# Vision


DEFAULT_TTL = {Priority.CRITICAL: 5.0, Priority.HIGH: 3.0, Priority.NORMAL: 3.0, Priority.FILLER: 6.0}


def event(type: str, priority: Priority | str, t: float, *, ttl: float | None = None, detail: str = "") -> GameEvent:
    priority = Priority(priority)
    return GameEvent(type, priority, ttl if ttl is not None else DEFAULT_TTL[priority], detail or type, t, "fake")


def frame(t: float, *events: GameEvent, scene: str | None = None, **fields: Any) -> VisionResult:
    return VisionResult(GameState(fields, scene), tuple(events), frame_t=t)


class FakeVisionSource:
    """Scripted results, delivered `latency_s` after their screenshot was captured."""

    def __init__(self, results: Iterable[VisionResult], latency_s: float = 1.0) -> None:
        self._results = sorted(results, key=lambda r: r.frame_t)
        self.latency_s = latency_s

    def results(self) -> list[VisionResult]:
        return list(self._results)

    def due(self, now: float) -> list[VisionResult]:
        ready = [r for r in self._results if r.frame_t + self.latency_s <= now]
        self._results = self._results[len(ready) :]
        return ready


# Narrator


class FakeNarrator:
    """Answers each request after `latency_s` with a line of `max_words` words (or `words(req)`)."""

    def __init__(
        self,
        clock: FakeClock,
        *,
        latency_s: float = 0.6,
        words: Callable[[NarrationRequest], int] | None = None,
        silent_modes: Iterable[Mode] = (),
    ) -> None:
        self._clock = clock
        self.latency_s = latency_s
        self._words = words
        self._silent = set(silent_modes)
        self.requests: list[NarrationRequest] = []
        self._due: list[tuple[float, NarrationRequest]] = []
        self.reply: Callable[[CommentaryLine], None] = lambda line: None

    def request(self, req: NarrationRequest) -> None:
        self.requests.append(req)
        self._due.append((self._clock() + self.latency_s, req))

    def step(self, now: float) -> None:
        ready = [(t, r) for t, r in self._due if t <= now]
        self._due = [(t, r) for t, r in self._due if t > now]
        for _, req in ready:
            self.reply(self.write(req, now))

    def write(self, req: NarrationRequest, now: float) -> CommentaryLine:
        if req.mode in self._silent:
            text = None
        else:
            n = max(1, self._words(req) if self._words else req.max_words)
            label = f"{req.mode.value}:{req.event.type if req.event else 'colour'}"
            text = " ".join([label] + ["word"] * (n - 1))
        return CommentaryLine(req.id, text, req.event, req.priority, req.mode, req.delivery, now, req.expires_at)


# Voice


@dataclass
class _Playing:
    line: CommentaryLine
    start: float
    end: float
    started: bool = False
    ending_sent: bool = False
    done: bool = False
    interrupted: bool = False


class FakeVoice:
    """Plays lines in simulated time and reports SpeechStatus like the real voice.

    - A line starts `tts_latency_s` after it's handed over and lasts words / `words_per_second`.
    - Hard interrupt: the current line stops after one audio chunk, then the new one starts.
    - Soft interrupt: the current line stops at the end of its sentence (`sentence_s` at most).
    - Playing a queued line while another plays overlaps them, so the fluency report catches it.
    """

    def __init__(
        self,
        clock: FakeClock,
        *,
        words_per_second: float = 2.8,
        tts_latency_s: float = 0.3,
        chunk_s: float = 0.1,
        sentence_s: float = 0.8,
        ending_s: float = 0.5,
    ) -> None:
        self._clock = clock
        self.words_per_second = words_per_second
        self.tts_latency_s = tts_latency_s
        self.chunk_s = chunk_s
        self.sentence_s = sentence_s
        self.ending_s = ending_s
        self.calls: list[tuple[str, CommentaryLine]] = []
        self._items: list[_Playing] = []
        self.report: Callable[[SpeechStatus], None] = lambda status: None

    def play(self, line: CommentaryLine) -> None:
        self.calls.append(("play", line))
        now = self._clock()
        current = self._active()
        if current is not None and line.delivery is Delivery.SOFT_INTERRUPT:
            self._cut(current, min(current.end, now + self.sentence_s))
            start = max(current.end, now) + self.tts_latency_s
        else:
            start = now + self.tts_latency_s
        self._add(line, start)

    def interrupt(self, line: CommentaryLine) -> None:
        self.calls.append(("interrupt", line))
        now = self._clock()
        current = self._active()
        if current is not None:
            self._cut(current, now + self.chunk_s)
        self._add(line, now + self.chunk_s + self.tts_latency_s)  # interrupt sound, then the line

    def step(self, now: float) -> None:
        for item in self._items:
            line_id = item.line.request_id
            if not item.started and not item.done and now >= item.start and item.start < item.end:
                item.started = True
                self.report(SpeechStatus(line_id, SpeechState.STARTED, item.start, duration_s=item.end - item.start))
            if item.started and not item.ending_sent and not item.done and item.end - now <= self.ending_s:
                item.ending_sent = True
                self.report(SpeechStatus(line_id, SpeechState.ENDING, now, remaining_s=max(0.0, item.end - now)))
            if not item.done and now >= item.end:
                item.done = True
                state = SpeechState.INTERRUPTED if item.interrupted else SpeechState.FINISHED
                self.report(SpeechStatus(line_id, state, item.end))

    def spoken(self) -> list[SpokenLine]:
        return [
            SpokenLine(i.line.request_id, i.line.text or "", i.line.priority, i.start, i.end, i.interrupted)
            for i in self._items
            if i.started
        ]

    def _active(self) -> _Playing | None:
        live = [i for i in self._items if not i.done]
        return live[-1] if live else None

    def _add(self, line: CommentaryLine, start: float) -> None:
        words = len((line.text or "").split())
        self._items.append(_Playing(line, start, start + words / self.words_per_second))

    def _cut(self, item: _Playing, at: float) -> None:
        if at < item.end:
            item.end = max(at, item.start) if item.started else at
            item.interrupted = True
            if not item.started:
                item.start = item.end  # never heard


# Simulation


class Simulation:
    def __init__(
        self,
        results: Iterable[VisionResult],
        persona: PersonaConfig,
        *,
        game: GameConfig | None = None,
        memory: Callable[[], Sequence[str]] = lambda: (),
        vision_latency_s: float = 1.0,
        narrator_latency_s: float = 0.6,
        tts_latency_s: float = 0.3,
        narrator_words: Callable[[NarrationRequest], int] | None = None,
        silent_modes: Iterable[Mode] = (),
        dt: float = 0.05,
    ) -> None:
        self.clock = FakeClock()
        self.bus = ListBus()
        self.vision = FakeVisionSource(results, vision_latency_s)
        self.narrator = FakeNarrator(
            self.clock, latency_s=narrator_latency_s, words=narrator_words, silent_modes=silent_modes
        )
        self.voice = FakeVoice(
            self.clock, words_per_second=persona.chattiness.words_per_second, tts_latency_s=tts_latency_s
        )
        self.director = Director(
            self.voice, self.narrator, persona, game=game, memory=memory, clock=self.clock, event_bus=self.bus
        )
        self.narrator.reply = self.director.on_line
        self.voice.report = self.director.on_speech
        self.dt = dt

    def add(self, *results: VisionResult) -> None:
        self.vision = FakeVisionSource([*self.vision.results(), *results], self.vision.latency_s)

    def run(self, until: float) -> None:
        while self.clock.t < until - 1e-9:
            self.clock.t = round(self.clock.t + self.dt, 6)
            now = self.clock.t
            for result in self.vision.due(now):
                self.director.submit(result)
            self.narrator.step(now)
            self.voice.step(now)
            self.director.tick()

    def report(self) -> FluencyReport:
        return fluency_report(self.voice.spoken(), self.bus.events, self.clock.t)

    def spoken_texts(self) -> list[str]:
        return [s.text for s in sorted(self.voice.spoken(), key=lambda s: s.start)]


# Scenarios


class scenarios:  # noqa: N801  (a namespace)
    @staticmethod
    def boss_fight_ending_in_death() -> list[VisionResult]:
        """Boss appears, a few exchanges, low HP, then death."""
        results = [frame(float(t), boss_name="Margit", player_hp=100, boss_hp=100) for t in range(0, 30)]
        script = {
            1.0: [event("boss_appeared", "critical", 1.0)],
            6.0: [event("big_hit_landed", "high", 6.0)],
            8.0: [event("took_heavy_damage", "high", 8.0)],
            9.0: [event("big_hit_landed", "high", 9.0)],
            12.0: [event("healed", "normal", 12.0)],
            16.0: [event("took_heavy_damage", "high", 16.0)],
            17.0: [event("low_hp", "high", 17.0)],
            20.0: [event("you_died", "critical", 20.0)],
        }
        return [frame(r.frame_t, *script.get(r.frame_t, ()), **r.state.fields) for r in results]

    @staticmethod
    def burst_of_hits() -> list[VisionResult]:
        """Five big hits in two seconds."""
        return [frame(0.0, boss_name="Margit")] + [
            frame(1.0 + i * 0.5, event("big_hit_landed", "high", 1.0 + i * 0.5), boss_name="Margit") for i in range(5)
        ]

    @staticmethod
    def quiet_exploration(seconds: int = 40) -> list[VisionResult]:
        """Nothing happening except scene updates every 3 s."""
        return [
            frame(float(t), *([event("scene_update", "filler", float(t))] if t % 3 == 0 else []), region="Limgrave")
            for t in range(seconds)
        ]

    @staticmethod
    def heal_cancels_low_hp() -> list[VisionResult]:
        """Low HP, then a heal before the low-HP line can be spoken."""
        return [
            frame(0.0, event("scene_update", "filler", 0.0)),
            frame(1.0, event("low_hp", "high", 1.0), player_hp=20),
            frame(1.5, event("healed", "normal", 1.5), player_hp=70),
        ]
