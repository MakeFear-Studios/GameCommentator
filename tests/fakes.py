"""Test doubles shared by the detection and capture tests."""

from collections.abc import Mapping
from typing import Any


class RecordingBus:
    """Stands in for `commentator.logs.EventBus`; records events synchronously as dicts."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def publish(self, stage: str, type: str, data: Mapping[str, Any] | None = None, level: str = "info") -> None:
        self.events.append({"stage": stage, "type": type, "data": dict(data or {}), "level": level})
