"""Load game and persona configs.

Only the parts used so far: detection (`match`), the director (event catalogue,
`cancels`, `context_fields`, persona `chattiness`). The full pydantic loader
(task 0.7) grows from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from commentator.core import paths
from commentator.core.types import Mode, Priority

# Games


@dataclass(frozen=True)
class GameMatch:
    process: str | None = None  # executable name, e.g. "eldenring.exe" (most reliable)
    window_title: str | None = None  # substring of the window title (fallback)

    @property
    def is_empty(self) -> bool:
        return not self.process and not self.window_title


@dataclass(frozen=True)
class EventSpec:
    priority: Priority
    ttl: float
    cooldown: float = 0.0
    cancels: tuple[str, ...] = ()  # event types this event makes untrue, e.g. healed cancels low_hp


@dataclass(frozen=True)
class GameConfig:
    id: str  # file stem, e.g. "elden_ring"
    name: str  # display name, e.g. "ELDEN RING"
    match: GameMatch
    events: Mapping[str, EventSpec] = field(default_factory=dict)
    # State fields that define the situation (e.g. boss_name): if one changes, lines written before are stale.
    context_fields: tuple[str, ...] = ()

    @property
    def cancels(self) -> dict[str, tuple[str, ...]]:
        return {name: spec.cancels for name, spec in self.events.items() if spec.cancels}


def _event_spec(data: Mapping[str, Any]) -> EventSpec:
    return EventSpec(
        priority=Priority(data.get("priority", "normal")),
        ttl=float(data.get("ttl", 3.0)),
        cooldown=float(data.get("cooldown", 0.0)),
        cancels=tuple(data.get("cancels") or ()),
    )


def load_game_config(path: Path) -> GameConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    match = data.get("match") or {}
    return GameConfig(
        id=path.stem,
        name=data.get("name", path.stem),
        match=GameMatch(process=match.get("process"), window_title=match.get("window_title")),
        events={name: _event_spec(spec or {}) for name, spec in (data.get("events") or {}).items()},
        context_fields=tuple(data.get("context_fields") or ()),
    )


def load_game_configs(directory: Path | None = None) -> list[GameConfig]:
    directory = directory or paths.games_dir()
    return [load_game_config(p) for p in sorted(directory.glob("*.yaml"))]


# Personas


def _default_max_words() -> dict[Mode, int]:
    # About 2–4 s each at 2.8 words/s; human commentators average about 2.8 s per line.
    return {Mode.REACTION: 6, Mode.PLAY_BY_PLAY: 10, Mode.COLOUR: 18, Mode.FILLER: 10}


def _default_cooldowns() -> dict[Priority, float]:
    return {Priority.CRITICAL: 0.0, Priority.HIGH: 0.0, Priority.NORMAL: 3.0, Priority.FILLER: 15.0}


@dataclass(frozen=True)
class Chattiness:
    """How much and how often the caster talks. All times in seconds."""

    min_gap_s: float = 1.5  # pause between lines
    max_silence_s: float = 6.0  # after this, a colour or filler line is allowed
    words_per_second: float = 2.8  # TTS speaking rate, to turn time into words
    max_words: Mapping[Mode, int] = field(default_factory=_default_max_words)
    # Minimum time since the last line of the same priority.
    cooldown_s: Mapping[Priority, float] = field(default_factory=_default_cooldowns)
    min_on_air_s: float = 0.8  # before anything but critical can interrupt a line
    interrupt_window_s: float = 4.0
    max_interrupts_in_window: int = 1  # non-critical
    soft_min_remaining_s: float = 1.5  # only soft-interrupt lines with more than this left
    after_critical_pause_s: float = 2.0  # deliberate silence after a critical line
    backlog_line_s: float = 3.0  # line length cap while more events are waiting, to catch up
    stack_cap: int = 5
    recent_lines: int = 5
    use_filler: bool = True


@dataclass(frozen=True)
class PersonaConfig:
    id: str
    name: str
    chattiness: Chattiness = field(default_factory=Chattiness)
    interrupt_sound: str = ""
    ignore_events: frozenset[str] = frozenset()  # from event_overrides: {type: {ignore: true}}
    raw: Mapping[str, Any] = field(default_factory=dict)  # style, voice, examples: used by the narrator


def _chattiness(data: Mapping[str, Any]) -> Chattiness:
    defaults = Chattiness()
    interrupts = data.get("interrupts") or {}
    max_words = dict(defaults.max_words)
    max_words.update({Mode(k): int(v) for k, v in (data.get("max_words") or {}).items()})
    cooldowns = dict(defaults.cooldown_s)
    cooldowns.update({Priority(k): float(v) for k, v in (data.get("cooldown_s") or {}).items()})

    def get(key: str, default: float) -> float:
        return float(data.get(key, default))

    return Chattiness(
        min_gap_s=get("min_gap_s", defaults.min_gap_s),
        max_silence_s=get("max_silence_s", defaults.max_silence_s),
        words_per_second=get("words_per_second", defaults.words_per_second),
        max_words=max_words,
        cooldown_s=cooldowns,
        min_on_air_s=float(interrupts.get("min_on_air_s", defaults.min_on_air_s)),
        interrupt_window_s=float(interrupts.get("window_s", defaults.interrupt_window_s)),
        max_interrupts_in_window=int(interrupts.get("max_in_window", defaults.max_interrupts_in_window)),
        soft_min_remaining_s=float(interrupts.get("soft_min_remaining_s", defaults.soft_min_remaining_s)),
        after_critical_pause_s=get("after_critical_pause_s", defaults.after_critical_pause_s),
        backlog_line_s=get("backlog_line_s", defaults.backlog_line_s),
        stack_cap=int(data.get("stack_cap", defaults.stack_cap)),
        recent_lines=int(data.get("recent_lines", defaults.recent_lines)),
        use_filler=bool(data.get("use_filler", defaults.use_filler)),
    )


def load_persona(path: Path) -> PersonaConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    overrides = data.get("event_overrides") or {}
    return PersonaConfig(
        id=path.stem,
        name=data.get("name", path.stem),
        chattiness=_chattiness(data.get("chattiness") or {}),
        interrupt_sound=data.get("interrupt_sound", ""),
        ignore_events=frozenset(k for k, v in overrides.items() if (v or {}).get("ignore")),
        raw=data,
    )


def load_personas(directory: Path | None = None) -> list[PersonaConfig]:
    directory = directory or paths.config_dir() / "personas"
    return [load_persona(p) for p in sorted(directory.glob("*.yaml"))]
