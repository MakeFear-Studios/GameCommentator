import pytest

from commentator.core.types import Priority
from commentator.director.sim import event
from commentator.director.stacks import PriorityStacks


def test_newest_first_within_a_level() -> None:
    stacks = PriorityStacks()
    stacks.push(event("took_heavy_damage", "high", 1.0))
    stacks.push(event("low_hp", "high", 2.0))
    stacks.push(event("big_hit_landed", "high", 3.0))

    top, rest = stacks.take(Priority.HIGH)

    assert top.event.type == "big_hit_landed"
    assert [e.event.type for e in rest] == ["low_hp", "took_heavy_damage"]
    assert stacks.peek(Priority.HIGH) is None  # the whole level is claimed


def test_priority_beats_recency() -> None:
    stacks = PriorityStacks()
    stacks.push(event("big_hit_landed", "high", 1.0))
    stacks.push(event("healed", "normal", 5.0))
    stacks.push(event("scene_update", "filler", 6.0))

    assert stacks.top_priority() is Priority.HIGH
    assert stacks.top_priority(exclude=[Priority.HIGH]) is Priority.NORMAL


def test_same_type_merges_keeping_newest() -> None:
    stacks = PriorityStacks()
    stacks.push(event("big_hit_landed", "high", 1.0))
    stacks.push(event("low_hp", "high", 1.5))

    result = stacks.push(event("big_hit_landed", "high", 2.0))

    assert result.action == "merged"
    assert result.entry.count == 2
    assert result.entry.event.t == 2.0
    assert [e.event.type for e in stacks.entries(Priority.HIGH)] == ["big_hit_landed", "low_hp"]
    assert len(stacks) == 2


def test_cap_drops_oldest() -> None:
    stacks = PriorityStacks(cap=2)
    stacks.push(event("a", "high", 1.0))
    stacks.push(event("b", "high", 2.0))

    result = stacks.push(event("c", "high", 3.0))

    assert [e.event.type for e in result.capped] == ["a"]
    assert [e.event.type for e in stacks.entries(Priority.HIGH)] == ["c", "b"]


def test_prune_uses_capture_time() -> None:
    stacks = PriorityStacks()
    stacks.push(event("old", "high", 1.0, ttl=2.0))  # expires at 3.0
    stacks.push(event("new", "high", 2.5, ttl=2.0))  # expires at 4.5

    expired = stacks.prune(now=3.5)

    assert [e.event.type for e in expired] == ["old"]
    assert [e.event.type for e in stacks.entries(Priority.HIGH)] == ["new"]


def test_out_of_order_events_are_sorted_by_capture_time() -> None:
    stacks = PriorityStacks()
    stacks.push(event("late", "high", 5.0))
    stacks.push(event("early", "high", 1.0))

    assert stacks.peek(Priority.HIGH).event.type == "late"


def test_restore_merges_with_newer_events() -> None:
    stacks = PriorityStacks()
    stacks.push(event("big_hit_landed", "high", 1.0))
    claimed, rest = stacks.take(Priority.HIGH)
    stacks.push(event("big_hit_landed", "high", 2.0))

    stacks.restore([claimed, *rest])

    entry = stacks.peek(Priority.HIGH)
    assert entry.count == 2 and entry.event.t == 2.0


def test_has_above_and_empty_take() -> None:
    stacks = PriorityStacks()
    stacks.push(event("healed", "normal", 1.0))

    assert stacks.has_above(Priority.FILLER)
    assert not stacks.has_above(Priority.NORMAL)
    with pytest.raises(LookupError):
        stacks.take(Priority.CRITICAL)
