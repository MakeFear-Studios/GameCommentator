from pathlib import Path

from commentator.core.config import Chattiness, load_game_config, load_persona, load_personas
from commentator.core.types import PRIORITIES_HIGH_TO_LOW, GameEvent, Mode, Priority


def test_priority_ranks_order_levels() -> None:
    assert [p.value for p in PRIORITIES_HIGH_TO_LOW] == ["critical", "high", "normal", "filler"]
    assert Priority.CRITICAL.rank > Priority.HIGH.rank > Priority.NORMAL.rank > Priority.FILLER.rank


def test_event_expires_from_capture_time() -> None:
    event = GameEvent("you_died", Priority.CRITICAL, ttl=5.0, detail="", t=10.0)
    assert event.expires_at == 15.0


def test_loads_repo_personas() -> None:
    personas = {p.id: p for p in load_personas()}

    caster = personas["sports_caster"]
    assert caster.name == "Sports Caster"
    assert caster.interrupt_sound == "OH—"
    assert caster.chattiness.min_gap_s == 1.5
    assert caster.chattiness.max_words[Mode.PLAY_BY_PLAY] == 10
    assert caster.chattiness.cooldown_s[Priority.FILLER] == 15.0
    assert caster.chattiness.min_on_air_s == 0.8
    assert caster.chattiness.max_interrupts_in_window == 1
    assert "examples" in caster.raw

    narrator = personas["dark_narrator"]
    assert narrator.ignore_events == frozenset({"healed"})
    assert narrator.chattiness.use_filler is False
    assert narrator.chattiness.after_critical_pause_s == 3.5


def test_persona_defaults_fill_gaps(tmp_path: Path) -> None:
    path = tmp_path / "quiet.yaml"
    path.write_text("chattiness:\n  max_words: { colour: 30 }\n  cooldown_s: { normal: 9 }\n", encoding="utf-8")

    chattiness = load_persona(path).chattiness

    defaults = Chattiness()
    assert chattiness.max_words[Mode.COLOUR] == 30
    assert chattiness.max_words[Mode.REACTION] == defaults.max_words[Mode.REACTION]
    assert chattiness.cooldown_s[Priority.NORMAL] == 9.0
    assert chattiness.cooldown_s[Priority.FILLER] == defaults.cooldown_s[Priority.FILLER]
    assert chattiness.min_gap_s == defaults.min_gap_s


def test_game_config_reads_event_catalogue_cancels_and_context(tmp_path: Path) -> None:
    path = tmp_path / "elden_ring.yaml"
    path.write_text(
        "name: ELDEN RING\n"
        "match: { process: eldenring.exe }\n"
        "context_fields: [boss_name]\n"
        "events:\n"
        "  healed: { priority: normal, ttl: 3.0, cooldown: 3, cancels: [low_hp, out_of_flasks] }\n"
        "  you_died: { priority: critical, ttl: 5.0 }\n",
        encoding="utf-8",
    )

    game = load_game_config(path)

    assert game.events["you_died"].priority is Priority.CRITICAL
    assert game.events["healed"].cooldown == 3.0
    assert game.cancels == {"healed": ("low_hp", "out_of_flasks")}
    assert game.context_fields == ("boss_name",)
