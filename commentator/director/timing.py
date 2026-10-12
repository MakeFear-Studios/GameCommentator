"""Running averages of how long a line takes to prepare, so it can be asked for early."""

from __future__ import annotations


class LatencyTracker:
    """Exponential moving averages of narrator and TTS latency.

    `lead_time` is how long before the voice is free the next line should be asked
    for, so it is ready the moment the current one ends.
    """

    def __init__(
        self, narrator_s: float = 0.8, tts_s: float = 0.3, *, alpha: float = 0.3, margin_s: float = 0.2
    ) -> None:
        self.narrator_s = narrator_s
        self.tts_s = tts_s
        self.alpha = alpha
        self.margin_s = margin_s

    def record_narrator(self, seconds: float) -> None:
        self.narrator_s = self._blend(self.narrator_s, seconds)

    def record_tts(self, seconds: float) -> None:
        self.tts_s = self._blend(self.tts_s, seconds)

    @property
    def lead_time(self) -> float:
        return self.narrator_s + self.tts_s + self.margin_s

    def _blend(self, average: float, sample: float) -> float:
        return (1 - self.alpha) * average + self.alpha * max(0.0, sample)
