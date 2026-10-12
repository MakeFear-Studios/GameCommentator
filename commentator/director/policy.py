"""Pure decisions: interrupt level, mode, line length, gap. No state, easy to test."""

from __future__ import annotations

from commentator.core.config import Chattiness
from commentator.core.types import Delivery, Mode, Priority


def interrupt_delivery(
    event_priority: Priority,
    *,
    current_priority: Priority,
    remaining_s: float,
    on_air_s: float,
    recent_interrupts: int,
    chattiness: Chattiness,
) -> Delivery | None:
    """How a waiting event may cut into the line on air, or None to wait for it to end.

    - Only a strictly more important event can interrupt (so an interrupting line is
      never cut off by another event of its own priority).
    - Critical: hard interrupt, always.
    - High: soft interrupt (after the current sentence), only if the line on air still
      has a lot left, has been on air long enough, and the interrupt budget allows it.
    """
    if event_priority.rank <= current_priority.rank:
        return None
    if event_priority is Priority.CRITICAL:
        return Delivery.HARD_INTERRUPT
    if event_priority is not Priority.HIGH:
        return None
    if remaining_s <= chattiness.soft_min_remaining_s:
        return None
    if on_air_s < chattiness.min_on_air_s:
        return None
    if recent_interrupts >= chattiness.max_interrupts_in_window:
        return None
    return Delivery.SOFT_INTERRUPT


def mode_for(priority: Priority) -> Mode:
    if priority is Priority.CRITICAL:
        return Mode.REACTION
    if priority is Priority.FILLER:
        return Mode.FILLER
    return Mode.PLAY_BY_PLAY


def max_words(mode: Mode, chattiness: Chattiness, *, backlog: bool) -> int:
    """The persona's cap for the mode; shorter while more events are waiting, to catch up."""
    cap = chattiness.max_words[mode]
    if backlog and mode is not Mode.REACTION:
        cap = min(cap, max(3, round(chattiness.words_per_second * chattiness.backlog_line_s)))
    return cap


def gap_after(priority: Priority | None, chattiness: Chattiness) -> float:
    """Silence to leave after a line of this priority ends: longer after big moments."""
    if priority is Priority.CRITICAL:
        return chattiness.after_critical_pause_s
    return chattiness.min_gap_s


def estimate_duration(text: str | None, chattiness: Chattiness) -> float:
    words = len(text.split()) if text else 0
    return words / chattiness.words_per_second
