"""Log event bus: every stage reports what it is doing here.

The bus is for observation only. Stages pass real data to each other through
their own queues; removing every subscriber must never change behaviour.

``publish`` never blocks the caller: events go into a bounded buffer and a
background thread hands them to subscribers. When the buffer is full the
oldest events are dropped and counted, so logging can never stall the game.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

LEVELS = ("debug", "info", "warning", "error")

Subscriber = Callable[["LogEvent"], None]


@dataclass(frozen=True)
class LogEvent:
    """One thing that happened in one stage."""

    seq: int
    """Increasing number; keeps order exact even when timestamps tie."""
    t: float
    """Seconds since the bus was created (monotonic clock)."""
    stage: str
    """Package that published it, e.g. ``"capture"`` or ``"director"``."""
    type: str
    """What happened, snake_case past tense, e.g. ``"event_dropped"``."""
    data: Mapping[str, Any] = field(default_factory=dict)
    level: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "t": round(self.t, 6),
            "stage": self.stage,
            "type": self.type,
            "level": self.level,
            "data": dict(self.data),
        }


class EventBus:
    """Thread-safe publish/subscribe bus for :class:`LogEvent`."""

    def __init__(
        self,
        max_buffer: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_buffer < 1:
            raise ValueError("max_buffer must be at least 1")
        self._clock = clock
        self._start = clock()
        self._cond = threading.Condition()
        self._buffer: deque[LogEvent] = deque()
        self._max_buffer = max_buffer
        self._subscribers: list[Subscriber] = []
        self._seq = 0
        self._dropped = 0
        self._dropped_reported = 0
        self._busy = False
        self._failed_subscribers: set[int] = set()
        self._thread: threading.Thread | None = None
        self._closed = False

    # -- time -------------------------------------------------------------

    def now(self) -> float:
        """Seconds since the bus was created, on the same clock as ``LogEvent.t``."""
        return self._clock() - self._start

    # -- publishing -------------------------------------------------------

    def publish(
        self,
        stage: str,
        type: str,
        data: Mapping[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        """Record that something happened. Returns immediately.

        Does nothing (beyond a cheap check) when there are no subscribers,
        so it is effectively free when dev mode is off.
        """
        if not self._subscribers or self._closed:
            return
        if level not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}, got {level!r}")
        with self._cond:
            self._enqueue(stage, type, data or {}, level)
            self._cond.notify()

    def _enqueue(self, stage: str, type: str, data: Mapping[str, Any], level: str) -> None:
        # Caller holds self._cond.
        self._seq += 1
        event = LogEvent(self._seq, self.now(), stage, type, data, level)
        if len(self._buffer) >= self._max_buffer:
            self._buffer.popleft()
            self._dropped += 1
        self._buffer.append(event)

    @property
    def dropped(self) -> int:
        """Total events dropped because the buffer was full."""
        return self._dropped

    # -- subscribing ------------------------------------------------------

    def subscribe(self, callback: Subscriber) -> None:
        """Call ``callback(event)`` for every published event, from the bus thread."""
        with self._cond:
            if self._closed:
                raise RuntimeError("bus is closed")
            if callback not in self._subscribers:
                self._subscribers = [*self._subscribers, callback]
            self._ensure_thread()

    def unsubscribe(self, callback: Subscriber) -> None:
        # Compare with == not `is`: each `obj.method` access creates a new bound-method object.
        with self._cond:
            self._subscribers = [s for s in self._subscribers if s != callback]

    def _ensure_thread(self) -> None:
        # Caller holds self._cond.
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="log-bus", daemon=True)
            self._thread.start()

    # -- delivery ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._buffer and not self._closed:
                    self._cond.wait()
                if not self._buffer and self._closed:
                    self._cond.notify_all()
                    return
                batch = list(self._buffer)
                self._buffer.clear()
                newly_dropped = self._dropped - self._dropped_reported
                if newly_dropped:
                    self._dropped_reported = self._dropped
                    self._seq += 1
                    batch.append(
                        LogEvent(
                            self._seq,
                            self.now(),
                            "logs",
                            "events_dropped",
                            {"count": newly_dropped, "total": self._dropped},
                            "warning",
                        )
                    )
                subscribers = self._subscribers
                self._busy = True
            try:
                for event in batch:
                    for callback in subscribers:
                        self._deliver(callback, event)
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

    def _deliver(self, callback: Subscriber, event: LogEvent) -> None:
        try:
            callback(event)
        except Exception:
            # Never re-publish here: a subscriber that always fails would loop forever.
            key = id(callback)
            if key not in self._failed_subscribers:
                self._failed_subscribers.add(key)
                print(f"[log-bus] subscriber {callback!r} failed; further errors suppressed", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)

    # -- lifecycle --------------------------------------------------------

    def flush(self, timeout: float | None = 5.0) -> bool:
        """Wait until every buffered event has been delivered. Returns False on timeout."""
        deadline = None if timeout is None else self._clock() + timeout
        with self._cond:
            while self._buffer or self._busy:
                if self._thread is None:
                    return not self._buffer
                remaining = None if deadline is None else deadline - self._clock()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)
            return True

    def close(self, timeout: float | None = 5.0) -> None:
        """Deliver what is buffered, then stop the bus thread. Further publishes are ignored."""
        self.flush(timeout)
        with self._cond:
            self._closed = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
