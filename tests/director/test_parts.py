"""History, speech tracking, latency averages and policy: the director's small parts."""

import pytest

from commentator.core.config import Chattiness
from commentator.core.types import CommentaryLine, Delivery, Mode, Priority, SpeechState, SpeechStatus
from commentator.director import policy
from commentator.director.history import StateHistory
from commentator.director.sim import event, frame
from commentator.director.speech import SpeechTracker
from commentator.director.timing import LatencyTracker

CH = Chattiness()


def line(request_id: int, priority: Priority = Priority.HIGH, text: str = "one two three") -> CommentaryLine:
    return CommentaryLine(request_id, text, None, priority, Mode.PLAY_BY_PLAY, Delivery.QUEUED, 0.0, 99.0)


# History


def test_history_tracks_latest_state_and_changes() -> None:
    history = StateHistory()
    history.record(frame(1.0, boss_hp=27, player_hp=80))
    baseline = history.latest
    history.record(frame(2.0, boss_hp=18, player_hp=80))

    assert history.latest.fields["boss_hp"] == 18
    assert history.change_since(baseline) == {"boss_hp": (27, 18)}
    assert history.change_since(None) == {"boss_hp": (None, 18), "player_hp": (None, 80)}


def test_history_remembers_when_events_were_seen() -> None:
    history = StateHistory()
    history.record(frame(1.0, event("low_hp", "high", 1.0)))
    history.record(frame(2.0, event("healed", "normal", 2.0)))

    assert history.seen_since({"healed"}, 1.5)
    assert not history.seen_since({"healed"}, 2.5)
    assert not history.seen_since({"you_died"}, 0.0)


def test_empty_history() -> None:
    assert StateHistory().latest is None
    assert StateHistory().change_since(None) == {}


# Speech tracking


def test_remaining_uses_estimate_then_real_duration_then_ending_hint() -> None:
    speech = SpeechTracker()
    speech.sent(line(1), now=0.0, estimated_s=3.0, interrupt=False)
    assert speech.speaking and speech.remaining(0.5) == 3.0  # not started yet

    speech.update(SpeechStatus(1, SpeechState.STARTED, 0.3, duration_s=2.0))
    assert speech.remaining(1.3) == pytest.approx(1.0)
    assert speech.current.on_air_s(1.3) == pytest.approx(1.0)

    speech.update(SpeechStatus(1, SpeechState.ENDING, 1.9, remaining_s=0.5))
    assert speech.remaining(2.1) == pytest.approx(0.3)


def test_finish_ends_the_line_and_ignores_other_ids() -> None:
    speech = SpeechTracker()
    speech.sent(line(1, Priority.CRITICAL), now=0.0, estimated_s=1.0, interrupt=False)

    assert speech.update(SpeechStatus(99, SpeechState.FINISHED, 0.5)) is None
    assert speech.speaking

    ended = speech.update(SpeechStatus(1, SpeechState.INTERRUPTED, 0.7))
    assert ended is not None and ended.line.request_id == 1
    assert not speech.speaking
    assert speech.last_ended_at == 0.7 and speech.last_priority is Priority.CRITICAL
    assert speech.remaining(1.0) == 0.0


def test_interrupts_are_counted_in_a_window() -> None:
    speech = SpeechTracker()
    speech.sent(line(1), now=1.0, estimated_s=1.0, interrupt=True)
    speech.sent(line(2), now=5.0, estimated_s=1.0, interrupt=True)
    speech.sent(line(3), now=6.0, estimated_s=1.0, interrupt=False)

    assert speech.interrupts_since(0.0) == 2
    assert speech.interrupts_since(2.0) == 1


# Latency


def test_lead_time_follows_measured_latency() -> None:
    latency = LatencyTracker(narrator_s=1.0, tts_s=0.5, alpha=0.5, margin_s=0.1)
    assert latency.lead_time == pytest.approx(1.6)

    latency.record_narrator(0.4)
    latency.record_tts(0.1)

    assert latency.narrator_s == pytest.approx(0.7)
    assert latency.tts_s == pytest.approx(0.3)
    assert latency.lead_time == pytest.approx(1.1)


# Policy


def soft(event_priority=Priority.HIGH, current=Priority.NORMAL, remaining=3.0, on_air=1.0, recent=0):
    return policy.interrupt_delivery(
        event_priority,
        current_priority=current,
        remaining_s=remaining,
        on_air_s=on_air,
        recent_interrupts=recent,
        chattiness=CH,
    )


def test_critical_hard_interrupts_anything_less_important() -> None:
    for current in (Priority.FILLER, Priority.NORMAL, Priority.HIGH):
        assert soft(Priority.CRITICAL, current, remaining=0.1, on_air=0.0, recent=9) is Delivery.HARD_INTERRUPT


def test_nothing_interrupts_its_own_priority() -> None:
    assert soft(Priority.CRITICAL, Priority.CRITICAL) is None
    assert soft(Priority.HIGH, Priority.HIGH) is None


def test_high_soft_interrupts_only_long_lines_on_air_long_enough_within_budget() -> None:
    assert soft() is Delivery.SOFT_INTERRUPT
    assert soft(remaining=CH.soft_min_remaining_s) is None  # nearly finished: just wait
    assert soft(on_air=CH.min_on_air_s - 0.1) is None  # only just started
    assert soft(recent=CH.max_interrupts_in_window) is None  # interrupt budget used


def test_normal_and_filler_never_interrupt() -> None:
    assert soft(Priority.NORMAL, Priority.FILLER) is None
    assert soft(Priority.FILLER, Priority.FILLER) is None


def test_modes_words_gaps_and_durations() -> None:
    assert policy.mode_for(Priority.CRITICAL) is Mode.REACTION
    assert policy.mode_for(Priority.HIGH) is Mode.PLAY_BY_PLAY
    assert policy.mode_for(Priority.NORMAL) is Mode.PLAY_BY_PLAY
    assert policy.mode_for(Priority.FILLER) is Mode.FILLER

    assert policy.max_words(Mode.COLOUR, CH, backlog=False) == CH.max_words[Mode.COLOUR]
    backlog_cap = round(CH.words_per_second * CH.backlog_line_s)
    assert policy.max_words(Mode.COLOUR, CH, backlog=True) == backlog_cap
    assert policy.max_words(Mode.REACTION, CH, backlog=True) == CH.max_words[Mode.REACTION]

    assert policy.gap_after(Priority.CRITICAL, CH) == CH.after_critical_pause_s
    assert policy.gap_after(Priority.HIGH, CH) == CH.min_gap_s
    assert policy.gap_after(None, CH) == CH.min_gap_s

    assert policy.estimate_duration("one two three four", Chattiness(words_per_second=2.0)) == 2.0
    assert policy.estimate_duration(None, CH) == 0.0
