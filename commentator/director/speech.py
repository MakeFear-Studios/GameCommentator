"""Track the line on air and how long it has left."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from commentator.core.types import CommentaryLine, Priority, SpeechState, SpeechStatus


@dataclass
class OnAir:
    line: CommentaryLine
    sent_at: float
    estimated_s: float  # from word count until the voice reports the real duration
    interrupt: bool  # whether this line interrupted another
    started_at: float | None = None
    duration_s: float | None = None
    remaining_hint: tuple[float, float] | None = None  # (t, remaining_s) from an ENDING status

    def remaining(self, now: float) -> float:
        if self.remaining_hint is not None:
            t, remaining = self.remaining_hint
            return max(0.0, remaining - (now - t))
        if self.started_at is not None and self.duration_s is not None:
            return max(0.0, self.started_at + self.duration_s - now)
        return self.estimated_s  # not started yet: all of it is still to come

    def on_air_s(self, now: float) -> float:
        return now - (self.started_at if self.started_at is not None else self.sent_at)


class SpeechTracker:
    """Follows SpeechStatus reports for the line the director last sent to the voice."""

    def __init__(self) -> None:
        self.current: OnAir | None = None
        self.last_ended_at: float | None = None
        self.last_priority: Priority | None = None
        self._interrupts: deque[float] = deque()

    @property
    def speaking(self) -> bool:
        return self.current is not None

    def sent(self, line: CommentaryLine, now: float, estimated_s: float, *, interrupt: bool) -> None:
        self.current = OnAir(line, now, estimated_s, interrupt)
        if interrupt:
            self._interrupts.append(now)

    def update(self, status: SpeechStatus) -> OnAir | None:
        """Apply a status. Returns the line if it just ended. Reports for replaced lines are ignored."""
        current = self.current
        if current is None or status.line_id != current.line.request_id:
            return None
        if status.state is SpeechState.STARTED:
            current.started_at = status.t
            current.duration_s = status.duration_s if status.duration_s is not None else current.estimated_s
        elif status.state is SpeechState.ENDING and status.remaining_s is not None:
            current.remaining_hint = (status.t, status.remaining_s)
        elif status.state in (SpeechState.FINISHED, SpeechState.INTERRUPTED):
            self.current = None
            self.last_ended_at = status.t
            self.last_priority = current.line.priority
            return current
        return None

    def remaining(self, now: float) -> float:
        return self.current.remaining(now) if self.current else 0.0

    def interrupts_since(self, t: float) -> int:
        while self._interrupts and self._interrupts[0] < t:
            self._interrupts.popleft()
        return len(self._interrupts)
