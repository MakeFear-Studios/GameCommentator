"""Shared types between tracks. Change only through a `contract` PR."""

import time
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Frame:
    """One captured picture of the game window."""

    image: np.ndarray  # height x width x 4, BGRA, uint8
    t: float = field(default_factory=time.monotonic)  # time.monotonic() when captured

    @property
    def width(self) -> int:
        return self.image.shape[1]

    @property
    def height(self) -> int:
        return self.image.shape[0]


class FrameSink(Protocol):
    """Where capture sends frames (the vision stage).

    Capture calls `submit` from its own thread for each new frame while the game is
    focused; paused and stale frames are never sent. `submit` must not block: keep
    only the newest frame and let the consumer take it when it's ready.
    """

    def submit(self, frame: Frame) -> None: ...


class NullFrameSink:
    """Accepts frames and does nothing. Used until the vision stage exists."""

    def submit(self, frame: Frame) -> None:
        pass
