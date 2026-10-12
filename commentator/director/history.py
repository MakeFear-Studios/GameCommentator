"""What the director remembers about recent screenshots: states and events seen."""

from __future__ import annotations

from collections import deque
from typing import Any

from commentator.core.types import GameState, VisionResult


class StateHistory:
    """Recent states and event sightings, newest last. Vision results carry no history; this is it."""

    def __init__(self, max_results: int = 120) -> None:
        self._states: deque[tuple[float, GameState]] = deque(maxlen=max_results)
        self._events: deque[tuple[float, str]] = deque(maxlen=max_results * 4)  # (capture t, type)

    def record(self, result: VisionResult) -> None:
        self._states.append((result.frame_t, result.state))
        for event in result.events:
            self._events.append((event.t, event.type))

    @property
    def latest(self) -> GameState | None:
        return self._states[-1][1] if self._states else None

    def change_since(self, baseline: GameState | None) -> dict[str, tuple[Any, Any]]:
        """Fields that differ between `baseline` (e.g. the state at the last line) and now."""
        latest = self.latest
        if latest is None:
            return {}
        before = baseline.fields if baseline else {}
        return {key: (before.get(key), value) for key, value in latest.fields.items() if before.get(key) != value}

    def seen_since(self, event_types: set[str], t: float) -> bool:
        """Whether any of these event types was captured at or after `t`."""
        return any(seen_t >= t and kind in event_types for seen_t, kind in reversed(self._events))
