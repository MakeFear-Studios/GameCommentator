"""Gather everything the narrator needs into one NarrationRequest."""

from __future__ import annotations

from collections.abc import Sequence

from commentator.core.config import Chattiness
from commentator.core.types import Delivery, GameEvent, GameState, Mode, NarrationRequest, Priority
from commentator.director import policy
from commentator.director.history import StateHistory
from commentator.director.stacks import StackEntry


def build_request(
    request_id: int,
    *,
    now: float,
    chosen: StackEntry | None,
    rest: Sequence[StackEntry],
    mode: Mode,
    delivery: Delivery,
    history: StateHistory,
    baseline: GameState | None,
    memory: Sequence[str],
    recent_lines: Sequence[str],
    chattiness: Chattiness,
    backlog: bool,
    extra_related: Sequence[GameEvent] = (),
) -> NarrationRequest:
    entries = ([chosen] if chosen else []) + list(rest)
    counts = {e.event.type: e.count for e in entries}
    if chosen is not None:
        priority = chosen.event.priority
        expires_at = chosen.expires_at
    else:  # colour line in a quiet spell: anything more important replaces it
        priority = Priority.FILLER
        expires_at = now + chattiness.max_silence_s
    return NarrationRequest(
        id=request_id,
        event=chosen.event if chosen else None,
        related=tuple(e.event for e in rest) + tuple(extra_related),
        counts=counts,
        state=history.latest or GameState(),
        state_change=history.change_since(baseline),
        memory=tuple(memory),
        recent_lines=tuple(recent_lines),
        mode=mode,
        priority=priority,
        max_words=policy.max_words(mode, chattiness, backlog=backlog),
        delivery=delivery,
        created_at=now,
        expires_at=expires_at,
    )
