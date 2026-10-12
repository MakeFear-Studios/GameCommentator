"""One newest-first stack per priority level."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from commentator.core.types import PRIORITIES_HIGH_TO_LOW, GameEvent, Priority


@dataclass(frozen=True)
class StackEntry:
    event: GameEvent  # the newest occurrence
    count: int = 1  # occurrences merged into this entry

    @property
    def expires_at(self) -> float:
        return self.event.expires_at


@dataclass(frozen=True)
class PushResult:
    action: str  # "pushed" | "merged"
    entry: StackEntry
    capped: tuple[StackEntry, ...] = ()  # entries dropped to keep the stack within its cap


class PriorityStacks:
    """Events waiting to be talked about.

    Each level is ordered by capture time, newest on top. A new event of a type
    already waiting at that level is merged into it (newest occurrence kept, count
    summed), so a burst becomes one entry. Each level keeps only `cap` entries.
    """

    def __init__(self, cap: int = 5) -> None:
        self.cap = cap
        self._stacks: dict[Priority, list[StackEntry]] = {p: [] for p in Priority}  # bottom ... top

    def push(self, event: GameEvent) -> PushResult:
        return self._insert(StackEntry(event))

    def restore(self, entries: Iterable[StackEntry]) -> None:
        """Put claimed entries back (e.g. when a prepared line is thrown away)."""
        for entry in entries:
            self._insert(entry)

    def _insert(self, entry: StackEntry) -> PushResult:
        stack = self._stacks[entry.event.priority]
        action = "pushed"
        for i, existing in enumerate(stack):
            if existing.event.type == entry.event.type:
                del stack[i]
                newer = entry.event if entry.event.t >= existing.event.t else existing.event
                entry = replace(entry, event=newer, count=entry.count + existing.count)
                action = "merged"
                break
        position = len(stack)
        while position > 0 and stack[position - 1].event.t > entry.event.t:
            position -= 1
        stack.insert(position, entry)
        capped: list[StackEntry] = []
        while len(stack) > self.cap:
            capped.append(stack.pop(0))  # oldest first
        return PushResult(action, entry, tuple(capped))

    def prune(self, now: float) -> list[StackEntry]:
        """Drop and return expired entries."""
        expired: list[StackEntry] = []
        for priority, stack in self._stacks.items():
            keep = [e for e in stack if e.expires_at >= now]
            expired.extend(e for e in stack if e.expires_at < now)
            self._stacks[priority] = keep
        return expired

    def peek(self, priority: Priority) -> StackEntry | None:
        stack = self._stacks[priority]
        return stack[-1] if stack else None

    def top_priority(self, *, exclude: Iterable[Priority] = ()) -> Priority | None:
        excluded = set(exclude)
        for priority in PRIORITIES_HIGH_TO_LOW:
            if priority not in excluded and self._stacks[priority]:
                return priority
        return None

    def take(self, priority: Priority) -> tuple[StackEntry, list[StackEntry]]:
        """Claim a whole level: its top entry and the rest (newest first) as context."""
        stack = self._stacks[priority]
        if not stack:
            raise LookupError(f"no {priority} events waiting")
        self._stacks[priority] = []
        newest_first = list(reversed(stack))
        return newest_first[0], newest_first[1:]

    def has_above(self, priority: Priority) -> bool:
        return any(self._stacks[p] for p in Priority if p.rank > priority.rank)

    def entries(self, priority: Priority) -> list[StackEntry]:
        """Newest first."""
        return list(reversed(self._stacks[priority]))

    def __len__(self) -> int:
        return sum(len(s) for s in self._stacks.values())
