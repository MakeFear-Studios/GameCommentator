"""Read a session log back, for replay, the dev dashboard and tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from .bus import LogEvent


def read_log(path: str | Path, strict: bool = False) -> Iterator[LogEvent]:
    """Yield the events in a log, oldest first.

    ``path`` can be an ``events.jsonl`` file or its session folder. Blank lines are
    skipped. Lines that aren't valid JSON (e.g. a half-written last line after a
    crash) are skipped unless ``strict`` is true.
    """
    path = Path(path)
    if path.is_dir():
        path = path / "events.jsonl"
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                yield LogEvent(
                    seq=raw["seq"],
                    t=raw["t"],
                    stage=raw["stage"],
                    type=raw["type"],
                    data=raw.get("data", {}),
                    level=raw.get("level", "info"),
                )
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                if strict:
                    raise ValueError(f"{path}:{number}: invalid log line") from error
