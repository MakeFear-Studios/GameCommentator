"""Load game configs.

Only the parts detection needs so far (name and `match`). The full pydantic
loader (task 0.7) grows from here.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from commentator.core import paths


@dataclass(frozen=True)
class GameMatch:
    process: str | None = None  # executable name, e.g. "eldenring.exe" (most reliable)
    window_title: str | None = None  # substring of the window title (fallback)

    @property
    def is_empty(self) -> bool:
        return not self.process and not self.window_title


@dataclass(frozen=True)
class GameConfig:
    id: str  # file stem, e.g. "elden_ring"
    name: str  # display name, e.g. "ELDEN RING"
    match: GameMatch


def load_game_config(path: Path) -> GameConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    match = data.get("match") or {}
    return GameConfig(
        id=path.stem,
        name=data.get("name", path.stem),
        match=GameMatch(process=match.get("process"), window_title=match.get("window_title")),
    )


def load_game_configs(directory: Path | None = None) -> list[GameConfig]:
    directory = directory or paths.games_dir()
    return [load_game_config(p) for p in sorted(directory.glob("*.yaml"))]
