"""Shared helpers for director tests."""

from commentator.core.config import Chattiness, EventSpec, GameConfig, GameMatch, PersonaConfig
from commentator.core.types import Priority


def persona(*, ignore: frozenset[str] = frozenset(), **chattiness) -> PersonaConfig:
    return PersonaConfig("test", "Test", Chattiness(**chattiness), ignore_events=ignore)


def game(*, cancels: dict[str, tuple[str, ...]] | None = None, context_fields: tuple[str, ...] = ()) -> GameConfig:
    events = {
        name: EventSpec(Priority.NORMAL, 3.0, cancels=tuple(cancelled)) for name, cancelled in (cancels or {}).items()
    }
    return GameConfig("test_game", "Test Game", GameMatch(process="test.exe"), events, context_fields)
