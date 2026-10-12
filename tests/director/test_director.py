"""Director behaviour, driven through the simulation with a fake clock.

Unless a test says otherwise, vision latency is 0 (events arrive at their capture
time), the narrator takes 0.6 s and TTS takes 0.3 s to start.
"""

import time

import pytest
from helpers import game, persona

from commentator.core.types import Delivery, Mode, Priority
from commentator.director import Director
from commentator.director.sim import FakeClock, FakeNarrator, FakeVoice, ListBus, Simulation, event, frame


def sim(results=(), *, p=None, **kwargs) -> Simulation:
    kwargs.setdefault("vision_latency_s", 0.0)
    return Simulation(list(results), p or persona(), **kwargs)


def types_of(bus_events, type_):
    return [e["data"] for e in bus_events if e["type"] == type_]


def starts(s: Simulation) -> list[tuple[float, str]]:
    return [(round(x.start, 2), x.text.split()[0]) for x in sorted(s.voice.spoken(), key=lambda x: x.start)]


# Choosing and building requests


def test_idle_director_asks_straight_away_with_context() -> None:
    s = sim(
        [
            frame(0.5, boss_hp=40, boss_name="Margit"),
            frame(
                1.0,
                event("took_heavy_damage", "high", 0.9, ttl=5),
                event("big_hit_landed", "high", 1.0, ttl=5),
                boss_hp=27,
                boss_name="Margit",
            ),
        ]
    )
    s.run(1.0)

    (req,) = s.narrator.requests
    assert req.event.type == "big_hit_landed"  # newest of the highest level
    assert [e.type for e in req.related] == ["took_heavy_damage"]
    assert req.counts == {"big_hit_landed": 1, "took_heavy_damage": 1}
    assert req.mode is Mode.PLAY_BY_PLAY and req.delivery is Delivery.QUEUED
    assert req.state.fields["boss_hp"] == 27
    assert req.state_change == {"boss_hp": (None, 27), "boss_name": (None, "Margit")}
    assert req.expires_at == pytest.approx(6.0)
    assert req.created_at == pytest.approx(1.0)


def test_state_change_and_recent_lines_are_relative_to_the_last_line() -> None:
    s = sim(
        [
            frame(1.0, event("big_hit_landed", "high", 1.0, ttl=5), boss_hp=40),
            frame(3.0, boss_hp=30),
            frame(5.0, event("low_hp", "high", 5.0, ttl=10), boss_hp=25),
        ]
    )
    s.run(8.0)

    first, second = s.narrator.requests
    assert second.recent_lines == (s.spoken_texts()[0],)
    assert second.state_change == {"boss_hp": (40, 25)}


def test_expired_events_are_never_requested() -> None:
    s = sim([frame(1.0, event("big_hit_landed", "high", 1.0, ttl=2.0))], vision_latency_s=2.5)
    s.run(5.0)

    assert s.narrator.requests == []
    assert types_of(s.bus.events, "event_dropped") == [{"event": "big_hit_landed", "reason": "expired", "count": 1}]


def test_persona_ignored_events_and_filler_off() -> None:
    s = sim(
        [frame(1.0, event("healed", "normal", 1.0), event("scene_update", "filler", 1.0))],
        p=persona(ignore=frozenset({"healed"}), use_filler=False),
    )
    s.run(2.0)

    reasons = {d["event"]: d["reason"] for d in types_of(s.bus.events, "event_dropped")}
    assert reasons == {"healed": "ignored", "scene_update": "filler_off"}
    assert s.narrator.requests == []


def test_repeats_of_the_event_being_talked_about_are_covered() -> None:
    hits = [frame(1.0 + i * 0.4, event("big_hit_landed", "high", 1.0 + i * 0.4)) for i in range(4)]
    s = sim(hits)
    s.run(10.0)

    assert len(s.narrator.requests) == 1
    assert [d["reason"] for d in types_of(s.bus.events, "event_dropped")] == ["covered"] * 3


def test_backlog_shortens_lines() -> None:
    p = persona(backlog_line_s=1.0)
    alone = sim([frame(1.0, event("big_hit_landed", "high", 1.0, ttl=9))], p=p)
    alone.run(1.0)
    busy = sim([frame(1.0, event("big_hit_landed", "high", 1.0, ttl=9), event("healed", "normal", 1.0, ttl=9))], p=p)
    busy.run(1.0)

    assert alone.narrator.requests[0].max_words == p.chattiness.max_words[Mode.PLAY_BY_PLAY]
    assert busy.narrator.requests[0].max_words == round(p.chattiness.words_per_second * 1.0)


# Timing and gaps


def test_gap_between_lines_matches_min_gap() -> None:
    s = sim(
        [
            frame(1.0, event("big_hit_landed", "high", 1.0, ttl=20)),
            frame(2.0, event("low_hp", "high", 2.0, ttl=20)),
        ]
    )
    s.run(15.0)

    first, second = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert second.start - first.end == pytest.approx(1.5, abs=0.1)
    assert s.report().overlap_pct == 0.0


def test_next_line_is_prepared_before_the_current_one_ends() -> None:
    s = sim(
        [
            frame(1.0, event("big_hit_landed", "high", 1.0, ttl=20)),
            frame(2.0, event("low_hp", "high", 2.0, ttl=20)),
        ],
        p=persona(min_gap_s=0.2),
    )
    s.run(15.0)

    first, second = sorted(s.voice.spoken(), key=lambda x: x.start)
    second_request = s.narrator.requests[1]
    assert second_request.created_at < first.end  # asked for while the first line was still playing
    # Queued lines are only handed over once the voice is free, so the shortest gap is TTS start-up (0.3 s).
    assert second.start - first.end == pytest.approx(0.3, abs=0.06)


def test_lead_time_adapts_to_a_slow_narrator() -> None:
    s = sim(
        [frame(1.0 + 6 * i, event(f"hit_{i}", "high", 1.0 + 6 * i, ttl=20)) for i in range(4)],
        p=persona(min_gap_s=0.2),
        narrator_latency_s=1.5,
    )
    before = s.director.latency.lead_time
    s.run(30.0)

    assert s.director.latency.narrator_s > 1.2
    assert s.director.latency.lead_time > before
    assert s.report().overlap_pct == 0.0


def test_cooldown_spaces_out_lines_of_the_same_priority() -> None:
    s = sim(
        [frame(1.0, event("healed", "normal", 1.0, ttl=20)), frame(1.5, event("dodged", "normal", 1.5, ttl=20))],
        p=persona(min_gap_s=0.0, cooldown_s={Priority.NORMAL: 8.0}),
    )
    s.run(20.0)

    first, second = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert second.start - first.start >= 8.0


# Interrupts


def test_critical_hard_interrupts_a_lower_line_within_one_tick() -> None:
    s = sim([frame(1.0, event("big_hit_landed", "high", 1.0)), frame(2.5, event("you_died", "critical", 2.5))])
    s.run(2.5)
    request = s.narrator.requests[-1]
    assert request.event.type == "you_died"
    assert request.delivery is Delivery.HARD_INTERRUPT and request.mode is Mode.REACTION
    assert request.created_at == pytest.approx(2.5)

    s.run(6.0)
    assert [kind for kind, _ in s.voice.calls] == ["play", "interrupt"]
    big_hit, death = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert big_hit.interrupted
    assert death.start == pytest.approx(2.5 + 0.6 + 0.1 + 0.3, abs=0.06)  # narrator, chunk, TTS
    assert types_of(s.bus.events, "interrupt") == [{"request": 2, "kind": "hard"}]


def test_critical_when_idle_skips_the_gap() -> None:
    s = sim([frame(1.0, event("big_hit_landed", "high", 1.0)), frame(5.0, event("you_died", "critical", 5.0))])
    s.run(10.0)

    hit, death = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert death.start - hit.end < 1.5  # sooner than min_gap
    assert [kind for kind, _ in s.voice.calls] == ["play", "play"]  # idle: no interrupt sound


def test_critical_does_not_interrupt_critical_but_follows_straight_after() -> None:
    s = sim(
        [
            frame(1.0, event("boss_appeared", "critical", 1.0, ttl=10)),
            frame(2.0, event("phase_two", "critical", 2.0, ttl=10)),
        ]
    )
    s.run(12.0)

    first, second = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert not first.interrupted
    assert first.end <= second.start < first.end + 0.5  # urgent: no post-critical pause
    assert types_of(s.bus.events, "interrupt") == []


def test_high_soft_interrupts_a_long_normal_line() -> None:
    s = sim([frame(1.0, event("healed", "normal", 1.0)), frame(3.0, event("big_hit_landed", "high", 3.0))])
    s.run(10.0)

    healed, hit = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert s.narrator.requests[1].delivery is Delivery.SOFT_INTERRUPT
    assert healed.interrupted
    assert hit.start < healed.start + 10 / 2.8  # before the normal line would have ended
    assert types_of(s.bus.events, "interrupt") == [{"request": 2, "kind": "soft"}]


def test_high_waits_when_the_line_is_nearly_done() -> None:
    s = sim(
        [frame(1.0, event("healed", "normal", 1.0)), frame(4.8, event("big_hit_landed", "high", 4.8, ttl=10))],
    )
    s.run(12.0)

    healed, hit = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert not healed.interrupted
    assert s.narrator.requests[1].delivery is Delivery.QUEUED


def test_high_waits_for_a_line_that_only_just_started() -> None:
    s = sim([frame(1.0, event("healed", "normal", 1.0)), frame(2.0, event("big_hit_landed", "high", 2.0, ttl=10))])
    s.run(2.1)  # healed line started at 1.9: on air for 0.2 s only

    assert len(s.narrator.requests) == 1
    s.run(3.0)
    assert s.narrator.requests[1].delivery is Delivery.SOFT_INTERRUPT


def test_interrupt_budget_limits_soft_interrupts() -> None:
    results = [frame(1.0, event("healed", "normal", 1.0))]
    results += [frame(3.0, event("big_hit_landed", "high", 3.0))]
    s = sim(results, p=persona(min_gap_s=0.0, max_interrupts_in_window=1, interrupt_window_s=30.0))
    s.run(6.0)
    # A second normal line, then another high event inside the window: no second interrupt.
    s.add(frame(9.0, event("dodged", "normal", 9.0)), frame(11.0, event("low_hp", "high", 11.0, ttl=10)))
    s.run(25.0)

    assert [d["kind"] for d in types_of(s.bus.events, "interrupt")] == ["soft"]
    assert s.report().overlap_pct == 0.0


# Re-checks and discarding


def test_prepared_line_is_superseded_by_a_more_important_event() -> None:
    s = sim(
        [frame(1.0, event("healed", "normal", 1.0, ttl=10)), frame(1.3, event("big_hit_landed", "high", 1.3, ttl=10))]
    )
    s.run(1.3)

    assert types_of(s.bus.events, "line_discarded") == [{"request": 1, "event": "healed", "reason": "superseded"}]
    s.run(1.35)
    assert s.narrator.requests[-1].event.type == "big_hit_landed"
    s.run(15.0)
    assert [text for _, text in starts(s)] == ["play_by_play:big_hit_landed", "play_by_play:healed"]


def newer_high_during_the_gap(at: float) -> Simulation:
    # big_hit line plays 1.9-5.47; low_hp's line is written at 5.5 and waits for the 3 s gap (speaks ~8.2).
    s = sim(
        [
            frame(1.0, event("big_hit_landed", "high", 1.0, ttl=20)),
            frame(2.0, event("low_hp", "high", 2.0, ttl=20)),
            frame(at, event("took_heavy_damage", "high", at, ttl=20)),
        ],
        p=persona(min_gap_s=3.0),
    )
    s.run(20.0)
    return s


def test_newer_same_priority_event_replaces_a_waiting_line_when_there_is_time() -> None:
    s = newer_high_during_the_gap(7.0)  # 1.5 s before speaking: enough to write a new line

    assert {"request": 2, "event": "low_hp", "reason": "superseded"} in types_of(s.bus.events, "line_discarded")
    assert s.narrator.requests[2].event.type == "took_heavy_damage"
    assert [e.type for e in s.narrator.requests[2].related] == ["low_hp"]
    assert s.report().overlap_pct == 0.0


def test_newer_same_priority_event_waits_when_there_is_no_time() -> None:
    s = newer_high_during_the_gap(8.0)  # too close to speaking: keep the written line

    assert types_of(s.bus.events, "line_discarded") == []
    assert [t for _, t in starts(s)][:2] == ["play_by_play:big_hit_landed", "play_by_play:low_hp"]


def test_late_narrator_reply_is_ignored() -> None:
    s = sim(
        [frame(1.0, event("healed", "normal", 1.0, ttl=10)), frame(1.2, event("big_hit_landed", "high", 1.2, ttl=10))]
    )
    s.run(5.0)

    assert {"request": 1, "reason": "late"} in types_of(s.bus.events, "line_discarded")
    assert "play_by_play:healed" not in [t for _, t in starts(s)][:1]


def test_line_dropped_if_it_expires_while_being_written() -> None:
    s = sim([frame(1.0, event("big_hit_landed", "high", 1.0, ttl=1.0))], narrator_latency_s=1.5)
    s.run(4.0)

    assert s.voice.calls == []
    assert types_of(s.bus.events, "line_discarded") == [{"request": 1, "event": "big_hit_landed", "reason": "expired"}]


def test_heal_makes_a_prepared_low_hp_line_stale() -> None:
    s = sim(
        [
            frame(1.0, event("big_hit_landed", "high", 1.0, ttl=10)),
            frame(2.0, event("low_hp", "high", 2.0, ttl=10), player_hp=20),
            frame(5.5, event("healed", "normal", 5.5, ttl=10), player_hp=70),
        ],
        game=game(cancels={"healed": ("low_hp",)}),
        p=persona(min_gap_s=0.2),
    )
    s.run(15.0)

    assert {"request": 2, "event": "low_hp", "reason": "stale_state"} in types_of(s.bus.events, "line_discarded")
    assert "play_by_play:low_hp" not in [t for _, t in starts(s)]


def test_context_change_makes_a_line_stale() -> None:
    s = sim(
        [
            frame(1.0, event("big_hit_landed", "high", 1.0, ttl=10), boss_name="Margit"),
            frame(2.0, event("low_hp", "high", 2.0, ttl=10), boss_name="Margit"),
            frame(5.5, boss_name=None),
        ],
        game=game(context_fields=("boss_name",)),
        p=persona(min_gap_s=0.2),
    )
    s.run(15.0)

    assert {"request": 2, "event": "low_hp", "reason": "stale_state"} in types_of(s.bus.events, "line_discarded")


def test_capped_events_are_dropped_and_logged() -> None:
    s = sim(
        [
            frame(1.0, event("boss_appeared", "critical", 1.0)),
            frame(1.5, event("a", "high", 1.5), event("b", "high", 1.6), event("c", "high", 1.7)),
        ],
        p=persona(stack_cap=2),
    )
    s.run(2.0)

    assert {"event": "a", "reason": "capped", "count": 1} in types_of(s.bus.events, "event_dropped")


def test_second_critical_drops_a_lower_line_prepared_behind_a_critical() -> None:
    # boss_appeared plays 1.9-4.04; with no post-critical pause the high line is asked for near its end.
    s = sim(
        [
            frame(1.0, event("boss_appeared", "critical", 1.0, ttl=20)),
            frame(2.0, event("big_hit_landed", "high", 2.0, ttl=20)),
            frame(3.0, event("phase_two", "critical", 3.0, ttl=20)),
        ],
        p=persona(after_critical_pause_s=0.0),
    )
    s.run(2.95)
    assert s.narrator.requests[-1].event.type == "big_hit_landed"

    s.run(15.0)
    assert {"request": 2, "event": "big_hit_landed", "reason": "superseded"} in types_of(s.bus.events, "line_discarded")
    texts = [t for _, t in starts(s)]
    assert texts[:2] == ["reaction:boss_appeared", "reaction:phase_two"]
    assert "play_by_play:big_hit_landed" in texts[2:]  # put back and spoken afterwards
    assert s.report().overlap_pct == 0.0


@pytest.mark.parametrize("critical_at", [6.0, 6.3])  # while the high line is being written / once written
def test_new_critical_replaces_a_line_waiting_in_the_gap(critical_at: float) -> None:
    s = sim(
        [
            frame(1.0, event("healed", "normal", 1.0, ttl=20)),
            frame(2.0, event("big_hit_landed", "high", 2.0, ttl=20)),
            frame(critical_at, event("you_died", "critical", critical_at)),
        ],
        p=persona(min_gap_s=4.0, min_on_air_s=99.0),  # no soft interrupt: the high line waits for the gap
    )
    s.run(critical_at)

    assert {"request": 2, "event": "big_hit_landed", "reason": "superseded"} in types_of(s.bus.events, "line_discarded")
    assert s.narrator.requests[-1].event.type == "you_died"
    assert s.narrator.requests[-1].delivery is Delivery.QUEUED  # voice idle: no interrupt needed
    s.run(20.0)
    assert [t for _, t in starts(s)] == ["play_by_play:healed", "reaction:you_died", "play_by_play:big_hit_landed"]


def test_recheck_drops_a_line_if_something_more_important_is_waiting() -> None:
    s = sim([frame(1.0, event("healed", "normal", 1.0, ttl=20))], p=persona(min_gap_s=0.0))
    s.run(1.55)  # healed line written at 1.6
    s.director.stacks.push(event("big_hit_landed", "high", 1.5, ttl=20))  # e.g. put back from a dropped line
    s.run(1.6)

    assert {"request": 1, "event": "healed", "reason": "superseded"} in types_of(s.bus.events, "line_discarded")
    assert s.voice.calls == []


# Quiet spells


def test_silence_is_chosen_when_there_is_nothing_to_say() -> None:
    s = sim([frame(1.0)], p=persona(max_silence_s=3.0))
    s.run(10.0)

    assert s.narrator.requests == []
    assert len(types_of(s.bus.events, "silence_chosen")) == 1  # announced once per quiet spell


def test_colour_line_from_memory_after_a_quiet_spell() -> None:
    s = sim([frame(1.0)], p=persona(max_silence_s=3.0), memory=lambda: ["Attempt 7 on Margit"])
    s.run(4.0)

    (req,) = s.narrator.requests
    assert req.mode is Mode.COLOUR and req.event is None
    assert req.memory == ("Attempt 7 on Margit",)
    assert req.priority is Priority.FILLER


def test_filler_only_after_a_quiet_spell_and_respecting_cooldown() -> None:
    results = [frame(float(t), event("scene_update", "filler", float(t))) for t in range(0, 40, 2)]
    s = sim(results, p=persona(max_silence_s=5.0, cooldown_s={Priority.FILLER: 12.0}))
    s.run(40.0)

    filler_starts = [t for t, text in starts(s) if text.startswith("filler")]
    assert filler_starts and filler_starts[0] >= 5.0
    assert all(b - a >= 12.0 for a, b in zip(filler_starts, filler_starts[1:], strict=False))


def test_narrator_may_say_nothing() -> None:
    s = sim([frame(1.0)], p=persona(max_silence_s=3.0), memory=lambda: ["fact"], silent_modes=[Mode.COLOUR])
    s.run(5.0)

    assert s.voice.calls == []
    assert types_of(s.bus.events, "silence_chosen") == [{"request": 1, "reason": "nothing_to_say"}]


def test_big_moments_get_a_pause_then_a_colour_reflection() -> None:
    s = sim(
        [frame(1.0, event("boss_felled", "critical", 1.0))],
        p=persona(after_critical_pause_s=2.0, max_silence_s=30.0),
        memory=lambda: ["Attempt 12 on Margit"],
    )
    s.run(10.0)

    felled, colour = sorted(s.voice.spoken(), key=lambda x: x.start)
    assert colour.start - felled.end == pytest.approx(2.0, abs=0.1)
    reflection = s.narrator.requests[1]
    assert reflection.mode is Mode.COLOUR
    assert [e.type for e in reflection.related] == ["boss_felled"]


# Threading


def test_runs_on_its_own_thread() -> None:
    clock = FakeClock()
    narrator = FakeNarrator(clock)
    voice = FakeVoice(clock)
    director = Director(voice, narrator, persona(), clock=clock, event_bus=ListBus(), tick_s=0.01)
    director.start()
    try:
        director.submit(frame(0.0, event("big_hit_landed", "high", 0.0, ttl=60)))
        deadline = time.monotonic() + 2
        while not narrator.requests and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        director.stop()

    assert narrator.requests and narrator.requests[0].event.type == "big_hit_landed"
