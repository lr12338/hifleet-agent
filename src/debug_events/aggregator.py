"""Server-side DebugEvent aggregator.

Consumes a run's events and validates structural invariants the UI and tests
rely on: monotonic seq, run_id consistency, tool started/completed/failed
pairing, answer.delta accumulation, duplicate event_id detection, and terminal
completion (incomplete_stream when no terminal event arrives).
"""
from __future__ import annotations

from typing import Any

from .contracts import DebugEvent, DebugEventType, TERMINAL_TYPES


class DebugAggregator:
    def __init__(self) -> None:
        self.events: list[DebugEvent] = []
        self._seen_ids: set[str] = set()
        self._open_tools: set[str] = set()
        self._answer_parts: list[str] = []
        self._terminal_type: str | None = None
        self._last_seq: int = -1
        self._run_id: str | None = None
        self.duplicate_ids: list[str] = []
        self.seq_violations: list[tuple[int, int]] = []
        self.run_id_mismatches: list[str] = []
        self.unpaired_tool_starts: list[str] = []
        self.unpaired_tool_completes: list[str] = []

    def feed(self, event: DebugEvent) -> None:
        if event.event_id in self._seen_ids:
            self.duplicate_ids.append(event.event_id)
        else:
            self._seen_ids.add(event.event_id)
        if event.seq <= self._last_seq:
            self.seq_violations.append((self._last_seq, event.seq))
        self._last_seq = max(self._last_seq, event.seq)
        if self._run_id is None:
            self._run_id = event.run_id
        elif event.run_id != self._run_id:
            self.run_id_mismatches.append(event.run_id)

        t = event.type
        if t == DebugEventType.TOOL_STARTED.value:
            if event.call_id:
                self._open_tools.add(event.call_id)
        elif t in (DebugEventType.TOOL_COMPLETED.value, DebugEventType.TOOL_FAILED.value):
            if event.call_id:
                if event.call_id in self._open_tools:
                    self._open_tools.discard(event.call_id)
                else:
                    self.unpaired_tool_completes.append(event.call_id)
        elif t == DebugEventType.ANSWER_DELTA.value:
            delta = event.data.get("delta")
            if isinstance(delta, str):
                self._answer_parts.append(delta)
        elif t in TERMINAL_TYPES:
            self._terminal_type = t
        self.events.append(event)

    @property
    def answer_text(self) -> str:
        return "".join(self._answer_parts)

    @property
    def is_complete(self) -> bool:
        return self._terminal_type is not None

    @property
    def is_incomplete_stream(self) -> bool:
        return self._terminal_type is None and bool(self.events)

    @property
    def terminal_type(self) -> str | None:
        return self._terminal_type

    def finalize(self) -> None:
        """Call after the stream ends to record dangling tool starts."""
        self.unpaired_tool_starts = sorted(self._open_tools)

    def summary(self) -> dict[str, Any]:
        tool_started = sum(1 for e in self.events if e.type == DebugEventType.TOOL_STARTED.value)
        tool_completed = sum(1 for e in self.events if e.type == DebugEventType.TOOL_COMPLETED.value)
        tool_failed = sum(1 for e in self.events if e.type == DebugEventType.TOOL_FAILED.value)
        return {
            "event_count": len(self.events),
            "run_id": self._run_id,
            "answer_length": len(self.answer_text),
            "tool_started": tool_started,
            "tool_completed": tool_completed,
            "tool_failed": tool_failed,
            "is_complete": self.is_complete,
            "is_incomplete_stream": self.is_incomplete_stream,
            "terminal_type": self.terminal_type,
            "duplicate_ids": self.duplicate_ids,
            "seq_violations": self.seq_violations,
            "run_id_mismatches": self.run_id_mismatches,
            "unpaired_tool_starts": self.unpaired_tool_starts,
            "unpaired_tool_completes": self.unpaired_tool_completes,
            "valid": (
                not self.duplicate_ids
                and not self.seq_violations
                and not self.run_id_mismatches
                and not self.unpaired_tool_completes
                and self.is_complete
            ),
        }


__all__ = ["DebugAggregator"]
