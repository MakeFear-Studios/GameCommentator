"""Fluency report: how natural the commentary timing was, as numbers.

Targets (docs/IMPLEMENTATION.md, B1): 0% overlap, mean gap under 2 s (or the
persona's own `min_gap_s` + 0.5 s if it's slower on purpose), at most 2
non-critical interrupts per minute.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from commentator.core.types import Priority

TALKING_GAP_LIMIT_S = 10.0  # longer silences are quiet spells, not gaps between lines


@dataclass(frozen=True)
class SpokenLine:
    line_id: int
    text: str
    priority: Priority
    start: float
    end: float
    interrupted: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class FluencyReport:
    duration_s: float
    lines: int
    overlap_pct: float  # share of speaking time with two lines at once
    mean_gap_s: float | None  # between consecutive lines, ignoring quiet spells
    longest_silence_s: float
    soft_interrupts: int
    hard_interrupts: int
    non_critical_interrupts_per_min: float
    dropped: Mapping[str, int]  # event_dropped reasons
    discarded: Mapping[str, int]  # line_discarded reasons

    def meets_targets(self, min_gap_s: float = 0.0) -> bool:
        """`min_gap_s`: the persona's intended pause; gaps up to half a second over it are fine."""
        gap_ok = self.mean_gap_s is None or self.mean_gap_s < max(2.0, min_gap_s + 0.5)
        return self.overlap_pct == 0.0 and gap_ok and self.non_critical_interrupts_per_min <= 2.0


def fluency_report(
    spoken: Sequence[SpokenLine], bus_events: Sequence[Mapping[str, Any]], duration_s: float
) -> FluencyReport:
    lines = sorted((s for s in spoken if s.duration > 0), key=lambda s: s.start)
    speaking = sum(s.duration for s in lines)
    overlap = _overlap_time(lines)
    gaps = [b.start - a.end for a, b in zip(lines, lines[1:], strict=False)]
    talking_gaps = [g for g in gaps if 0 <= g <= TALKING_GAP_LIMIT_S]
    silences = [lines[0].start] if lines else [duration_s]
    silences += [g for g in gaps if g > 0]
    if lines:
        silences.append(max(0.0, duration_s - max(s.end for s in lines)))
    director = [e for e in bus_events if e.get("stage") == "director"]
    kinds = Counter(e["data"].get("kind") for e in director if e["type"] == "interrupt")
    minutes = max(duration_s / 60.0, 1e-9)
    return FluencyReport(
        duration_s=duration_s,
        lines=len(lines),
        overlap_pct=round(100.0 * overlap / speaking, 2) if speaking else 0.0,
        mean_gap_s=round(sum(talking_gaps) / len(talking_gaps), 3) if talking_gaps else None,
        longest_silence_s=round(max(silences), 3),
        soft_interrupts=kinds["soft"],
        hard_interrupts=kinds["hard"],
        non_critical_interrupts_per_min=round(kinds["soft"] / minutes, 2),
        dropped=dict(Counter(e["data"].get("reason") for e in director if e["type"] == "event_dropped")),
        discarded=dict(Counter(e["data"].get("reason") for e in director if e["type"] == "line_discarded")),
    )


def _overlap_time(lines: Sequence[SpokenLine]) -> float:
    """Total time during which two or more lines were playing."""
    edges = sorted([(s.start, 1) for s in lines] + [(s.end, -1) for s in lines], key=lambda e: (e[0], e[1]))
    overlap, active, last_t = 0.0, 0, 0.0
    for t, change in edges:
        if active >= 2:
            overlap += t - last_t
        active += change
        last_t = t
    return overlap
