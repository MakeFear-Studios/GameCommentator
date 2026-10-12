"""Minimal event bus with the contract's signature.

Stand-in until task 0.4 replaces it; callers only use `publish` and `subscribe`.
"""

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

Record = dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Callable[[Record], None]] = []
        self._lock = threading.Lock()
        self._start = time.monotonic()

    def publish(self, stage: str, type: str, data: dict[str, Any] | None = None) -> None:
        record = {
            "t": round(time.monotonic() - self._start, 3),
            "stage": stage,
            "type": type,
            "data": data or {},
        }
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            callback(record)

    def subscribe(self, callback: Callable[[Record], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)


class JsonlLogger:
    """Writes one JSON object per line to `logs/<session>/events.jsonl`."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, record: Record) -> None:
        with self._lock:
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


bus = EventBus()
