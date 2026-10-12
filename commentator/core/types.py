"""Shared types between tracks. Change only through a `contract` PR."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import numpy as np

# Capture -> vision


@dataclass(frozen=True)
class Frame:
    """One captured picture of the game window."""

    image: np.ndarray  # height x width x 4, BGRA, uint8
    t: float = field(default_factory=time.monotonic)  # time.monotonic() when captured

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]


class FrameSink(Protocol):
    """Where capture sends frames (the vision stage).

    Capture calls `submit` from its own thread for each new frame while the game is
    focused; paused and stale frames are never sent. `submit` must not block: keep
    only the newest frame and let the consumer take it when it's ready.
    """

    def submit(self, frame: Frame) -> None: ...


class NullFrameSink:
    """Accepts frames and does nothing. Used until the vision stage exists."""

    def submit(self, frame: Frame) -> None:
        pass


# Vision -> director


class Priority(StrEnum):
    FILLER = "filler"  # only after a quiet spell
    NORMAL = "normal"  # waits for the voice to be free; expires quickly
    HIGH = "high"  # waits on its stack; may soft-interrupt a long, lower-priority line
    CRITICAL = "critical"  # hard-interrupts current speech

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self]


_PRIORITY_RANK = {Priority.FILLER: 0, Priority.NORMAL: 1, Priority.HIGH: 2, Priority.CRITICAL: 3}
PRIORITIES_HIGH_TO_LOW = sorted(Priority, key=lambda p: p.rank, reverse=True)


@dataclass(frozen=True)
class GameEvent:
    type: str  # must exist in the game config's event catalogue
    priority: Priority  # set by the vision stage (model picks from the game config, validation enforces it)
    ttl: float  # seconds; from the game config
    detail: str  # short description from the vision LLM
    t: float  # time.monotonic() when the screenshot was captured (Frame.t), not when it arrived
    source: str = "vision"  # "vision" | "replay" | "fake"

    @property
    def expires_at(self) -> float:
        return self.t + self.ttl


@dataclass(frozen=True)
class GameState:
    fields: Mapping[str, Any] = field(default_factory=dict)  # what this screenshot shows; keys from the game config
    scene: str | None = None  # one-sentence description of this screenshot


@dataclass(frozen=True)
class VisionResult:
    """What the vision stage saw in ONE screenshot. No history: the director keeps that."""

    state: GameState
    events: tuple[GameEvent, ...]
    frame_t: float  # Frame.t of the screenshot
    latency_ms: float = 0.0  # how long vision took


class VisionSink(Protocol):
    """Implemented by the director. `submit` must not block."""

    def submit(self, result: VisionResult) -> None: ...


class VisionSource(Protocol):
    """Recorded or scripted vision results, for running without the game."""

    def results(self) -> Iterator[VisionResult]: ...  # in capture-time order


# Director -> narrator -> voice


class Mode(StrEnum):
    REACTION = "reaction"  # critical moment: very short
    PLAY_BY_PLAY = "play_by_play"  # high / normal events during action
    COLOUR = "colour"  # quiet spell: analysis and stats from memory
    FILLER = "filler"  # scene remark when nothing else is happening


class Delivery(StrEnum):
    HARD_INTERRUPT = "hard_interrupt"  # cut current speech at the next audio chunk
    SOFT_INTERRUPT = "soft_interrupt"  # after the current sentence
    QUEUED = "queued"  # when the voice is free


@dataclass(frozen=True)
class NarrationRequest:
    id: int
    event: GameEvent | None  # top of the highest non-empty stack; None for a colour line in a quiet spell
    related: tuple[GameEvent, ...]  # rest of that stack, newest first
    counts: Mapping[str, int]  # event type -> times seen (merged repeats), e.g. {"big_hit_landed": 3}
    state: GameState  # latest state
    state_change: Mapping[str, tuple[Any, Any]]  # field -> (at the last line, now), e.g. {"boss_hp": (27, 18)}
    memory: tuple[str, ...]  # short facts from memory (B4); empty until it exists
    recent_lines: tuple[str, ...]  # last few spoken lines, to avoid repeats
    mode: Mode
    priority: Priority  # of the chosen event (FILLER for colour lines)
    max_words: int  # from mode, persona and time available
    delivery: Delivery
    created_at: float  # monotonic
    expires_at: float  # monotonic; don't speak after this


class Narrator(Protocol):
    """Writes one line per request. Must not block; replies through the director's `on_line`."""

    def request(self, req: NarrationRequest) -> None: ...


@dataclass(frozen=True)
class CommentaryLine:
    request_id: int
    text: str | None  # None = "nothing to say" (allowed for colour / filler)
    event: GameEvent | None
    priority: Priority
    mode: Mode
    delivery: Delivery
    created_at: float
    expires_at: float


class SpeechState(StrEnum):
    STARTED = "started"
    ENDING = "ending"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class SpeechStatus:
    line_id: int  # CommentaryLine.request_id
    state: SpeechState
    t: float  # monotonic
    duration_s: float | None = None  # on STARTED: exact audio length from TTS
    remaining_s: float | None = None  # on ENDING: time left


class Voice(Protocol):
    """Speaks lines. Reports SpeechStatus through the director's `on_speech`."""

    # QUEUED: the voice is free. SOFT_INTERRUPT: after the current sentence.
    def play(self, line: CommentaryLine) -> None: ...

    def interrupt(self, line: CommentaryLine) -> None: ...  # cut now, interrupt sound, then the line
