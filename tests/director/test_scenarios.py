"""Whole scenarios through the simulation, judged by the fluency report.

These use realistic latencies: vision 1 s, narrator 0.6 s, TTS 0.3 s.
"""

import pytest

from commentator.core.config import load_personas
from commentator.core.types import CommentaryLine, Delivery, Mode, Priority
from commentator.director.fluency import SpokenLine, fluency_report
from commentator.director.sim import FakeClock, FakeVoice, Simulation, event, frame, scenarios

PERSONAS = {p.id: p for p in load_personas()}


@pytest.fixture(params=sorted(PERSONAS))
def persona(request):
    return PERSONAS[request.param]


def run(results, persona, until, **kwargs) -> Simulation:
    sim = Simulation(results, persona, **kwargs)
    sim.run(until)
    return sim


# Fluency targets on every scenario, for every persona


@pytest.mark.parametrize(
    ("scenario", "until"),
    [
        (scenarios.boss_fight_ending_in_death, 35.0),
        (scenarios.burst_of_hits, 15.0),
        (scenarios.quiet_exploration, 40.0),
        (scenarios.heal_cancels_low_hp, 10.0),
    ],
)
def test_scenarios_meet_fluency_targets(scenario, until, persona) -> None:
    report = run(scenario(), persona, until).report()

    max_gap = max(2.0, persona.chattiness.min_gap_s + 0.5)  # slow personas pause longer on purpose
    assert report.overlap_pct == 0.0
    assert report.mean_gap_s is None or report.mean_gap_s < max_gap
    assert report.non_critical_interrupts_per_min <= 2.0
    assert report.meets_targets(persona.chattiness.min_gap_s)


def test_critical_events_reach_the_voice_fast(persona) -> None:
    sim = run(scenarios.boss_fight_ending_in_death(), persona, 35.0)

    critical = {s.text.split()[0]: s.start for s in sim.voice.spoken() if s.priority is Priority.CRITICAL}
    # capture -> speech: vision 1.0 + narrator 0.6 + (interrupt chunk 0.1) + TTS 0.3 + one tick
    assert critical["reaction:boss_appeared"] - 1.0 <= 1.0 + 0.6 + 0.3 + 0.05 + 1e-6
    assert critical["reaction:you_died"] - 20.0 <= 1.0 + 0.6 + 0.1 + 0.3 + 0.05 + 1e-6


def test_boss_fight_story() -> None:
    sim = run(scenarios.boss_fight_ending_in_death(), PERSONAS["sports_caster"], 35.0)
    texts = [t.split()[0] for t in sim.spoken_texts()]

    assert texts[0] == "reaction:boss_appeared"
    assert texts[-1] == "reaction:you_died"
    assert "play_by_play:big_hit_landed" in texts
    assert sim.report().hard_interrupts == 1  # the death cut off a line


def test_burst_of_hits_becomes_one_line(persona) -> None:
    sim = run(scenarios.burst_of_hits(), persona, 15.0)

    assert [t.split()[0] for t in sim.spoken_texts()] == ["play_by_play:big_hit_landed"]
    assert sim.report().dropped == {"covered": 4}


def test_quiet_exploration_stays_mostly_quiet() -> None:
    caster = run(scenarios.quiet_exploration(), PERSONAS["sports_caster"], 40.0)
    narrator = run(scenarios.quiet_exploration(), PERSONAS["dark_narrator"], 40.0)

    caster_lines = caster.voice.spoken()
    assert 1 <= len(caster_lines) <= 3  # filler every ~15 s at most
    assert all(s.priority is Priority.FILLER for s in caster_lines)
    assert narrator.voice.spoken() == []  # the dark narrator doesn't do filler
    assert narrator.report().dropped == {"filler_off": 14}


def test_heal_cancels_low_hp_in_the_game_config() -> None:
    from helpers import game

    sim = run(
        scenarios.heal_cancels_low_hp(), PERSONAS["sports_caster"], 10.0, game=game(cancels={"healed": ("low_hp",)})
    )
    texts = [t.split()[0] for t in sim.spoken_texts()]

    assert sim.report().discarded == {"stale_state": 1}  # the low-HP line, written before the heal was seen
    assert texts == ["play_by_play:healed"]


def test_dark_narrator_ignores_heals() -> None:
    sim = run([frame(1.0, event("healed", "normal", 1.0))], PERSONAS["dark_narrator"], 5.0, narrator_latency_s=0.6)

    assert sim.voice.spoken() == []
    assert sim.report().dropped == {"ignored": 1}


def test_colour_lines_fill_long_quiet_spells_when_memory_has_something() -> None:
    sim = run(
        scenarios.quiet_exploration(), PERSONAS["dark_narrator"], 40.0, memory=lambda: ["Third visit to Limgrave"]
    )

    modes = [r.mode for r in sim.narrator.requests]
    assert modes and set(modes) == {Mode.COLOUR}
    assert sim.report().meets_targets(PERSONAS["dark_narrator"].chattiness.min_gap_s)


# Fluency report


def spoken(start, end, priority=Priority.HIGH, interrupted=False, line_id=1):
    return SpokenLine(line_id, "text", priority, start, end, interrupted)


def test_report_measures_gaps_overlap_and_silence() -> None:
    lines = [spoken(1.0, 3.0), spoken(4.0, 6.0), spoken(5.0, 7.0), spoken(30.0, 31.0)]

    report = fluency_report(lines, [], duration_s=40.0)

    assert report.lines == 4
    assert report.overlap_pct == pytest.approx(100 * 1.0 / 7.0, abs=0.01)
    assert report.mean_gap_s == pytest.approx(1.0)  # the 23 s gap is a quiet spell, not a gap
    assert report.longest_silence_s == pytest.approx(23.0)
    assert not report.meets_targets()


def test_report_counts_interrupts_and_drops() -> None:
    events = [
        {"stage": "director", "type": "interrupt", "data": {"kind": "soft"}},
        {"stage": "director", "type": "interrupt", "data": {"kind": "hard"}},
        {"stage": "director", "type": "event_dropped", "data": {"reason": "expired"}},
        {"stage": "director", "type": "event_dropped", "data": {"reason": "expired"}},
        {"stage": "director", "type": "line_discarded", "data": {"reason": "stale_state"}},
        {"stage": "capture", "type": "interrupt", "data": {"kind": "soft"}},  # other stages ignored
    ]

    report = fluency_report([spoken(0.0, 1.0)], events, duration_s=30.0)

    assert (report.soft_interrupts, report.hard_interrupts) == (1, 1)
    assert report.non_critical_interrupts_per_min == pytest.approx(2.0)
    assert report.dropped == {"expired": 2}
    assert report.discarded == {"stale_state": 1}


def test_empty_report() -> None:
    report = fluency_report([], [], duration_s=10.0)

    assert report.lines == 0 and report.mean_gap_s is None
    assert report.longest_silence_s == 10.0
    assert report.meets_targets()


# Fake voice (it's what the targets are measured with, so it must behave)


def make_line(request_id, words, priority=Priority.HIGH, delivery=Delivery.QUEUED):
    text = " ".join(["w"] * words)
    return CommentaryLine(request_id, text, None, priority, Mode.PLAY_BY_PLAY, delivery, 0.0, 99.0)


def run_voice(voice, clock, until, statuses):
    while clock.t < until:
        clock.t = round(clock.t + 0.05, 6)
        voice.step(clock.t)


def test_fake_voice_reports_start_ending_finish() -> None:
    clock = FakeClock()
    voice = FakeVoice(clock, words_per_second=2.0, tts_latency_s=0.3)
    statuses = []
    voice.report = statuses.append
    voice.play(make_line(1, 4))  # 2 s long
    run_voice(voice, clock, 3.0, statuses)

    assert [s.state.value for s in statuses] == ["started", "ending", "finished"]
    assert statuses[0].t == pytest.approx(0.3) and statuses[0].duration_s == pytest.approx(2.0)
    assert statuses[-1].t == pytest.approx(2.3)


def test_fake_voice_hard_and_soft_interrupts() -> None:
    clock = FakeClock()
    voice = FakeVoice(clock, words_per_second=2.0, tts_latency_s=0.3, chunk_s=0.1, sentence_s=0.8)
    statuses = []
    voice.report = statuses.append
    voice.play(make_line(1, 10))
    run_voice(voice, clock, 1.0, statuses)
    voice.play(make_line(2, 2, delivery=Delivery.SOFT_INTERRUPT))
    run_voice(voice, clock, 2.5, statuses)
    voice.interrupt(make_line(3, 2, priority=Priority.CRITICAL))
    run_voice(voice, clock, 6.0, statuses)

    first, second, third = voice.spoken()
    assert first.interrupted and first.end == pytest.approx(1.8)  # end of its sentence
    assert second.start == pytest.approx(2.1) and second.interrupted
    assert second.end == pytest.approx(2.6)  # one chunk after the hard interrupt
    assert third.start == pytest.approx(2.9) and not third.interrupted
    assert fluency_report(voice.spoken(), [], 6.0).overlap_pct == 0.0


def test_fake_voice_overlaps_if_told_to_play_while_busy() -> None:
    clock = FakeClock()
    voice = FakeVoice(clock, words_per_second=2.0, tts_latency_s=0.0)
    voice.play(make_line(1, 4))
    run_voice(voice, clock, 1.0, [])
    voice.play(make_line(2, 4))
    run_voice(voice, clock, 5.0, [])

    assert fluency_report(voice.spoken(), [], 5.0).overlap_pct > 0
