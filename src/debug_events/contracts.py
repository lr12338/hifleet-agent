"""DebugEvent V1 contracts.

A single, provider-agnostic event model for the dialogue test workbench. The
admin UI renders strictly by ``DebugEvent.type``; it must never guess that an
arbitrary provider packet is an answer. Events describe *what really happened*
(runtime state, real tool calls, real provider deltas) and never fabricate
hidden chain-of-thought.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEBUG_EVENT_SCHEMA_VERSION = "debug-event.v1"


class DebugEventType(str, Enum):
    RUN_STARTED = "run.started"
    INPUT_NORMALIZED = "input.normalized"
    ROUTE_SELECTED = "route.selected"
    PHASE_STARTED = "phase.started"
    PHASE_COMPLETED = "phase.completed"
    REASONING_SUMMARY = "reasoning.summary"
    TOOL_STARTED = "tool.started"
    TOOL_ARGUMENTS_DELTA = "tool.arguments.delta"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    EVIDENCE_SUMMARY = "evidence.summary"
    GUARD_RESULT = "guard.result"
    ANSWER_STARTED = "answer.started"
    ANSWER_DELTA = "answer.delta"
    ANSWER_COMPLETED = "answer.completed"
    RUN_COMPLETED = "run.completed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMEOUT = "run.timeout"
    RUN_FAILED = "run.failed"
    HEARTBEAT = "heartbeat"
    RAW_PROVIDER_EVENT = "raw_provider_event"


class EventPhase(str, Enum):
    INTAKE = "intake"
    UNDERSTANDING = "understanding"
    ROUTING = "routing"
    EVIDENCE_RETRIEVAL = "evidence_retrieval"
    TOOL_EXECUTION = "tool_execution"
    GUARD = "guard"
    RESPONSE_SYNTHESIS = "response_synthesis"
    FINALIZATION = "finalization"


class EventRuntime(str, Enum):
    CHAT = "chat"
    CHAT_FUNCTION_CALLING = "chat_function_calling"
    RESPONSES = "responses"
    LANGGRAPH = "langgraph"


class EventVisibility(str, Enum):
    ADMIN_SAFE = "admin_safe"
    CUSTOMER_SAFE = "customer_safe"
    INTERNAL_DEBUG = "internal_debug"


# Events that are safe to surface to external /run and /stream_run consumers.
CUSTOMER_VISIBLE_TYPES = frozenset(
    {
        DebugEventType.RUN_STARTED.value,
        DebugEventType.ANSWER_DELTA.value,
        DebugEventType.ANSWER_COMPLETED.value,
        DebugEventType.RUN_COMPLETED.value,
        DebugEventType.RUN_CANCELLED.value,
        DebugEventType.RUN_TIMEOUT.value,
        DebugEventType.RUN_FAILED.value,
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DebugEvent(BaseModel):
    """A single normalized debug event.

    ``seq`` is strictly monotonically increasing within a run and is assigned by
    :class:`DebugEmitter`. ``run_id`` ties every event to a run. Tool events
    carry a stable ``call_id`` so ``tool.started``/``tool.completed``/``tool.failed``
    can be paired. ``answer.delta`` carries only incremental text; the full text
    (or its hash/length) is reported in ``answer.completed``.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: str = Field(default=DEBUG_EVENT_SCHEMA_VERSION)
    event_id: str
    seq: int = Field(ge=0)
    timestamp: str = Field(default_factory=_now_iso)
    run_id: str
    type: DebugEventType
    # Context (optional but populated by the emitter when known).
    session_id: str | None = None
    agent_profile: str | None = None
    endpoint: str | None = None
    runtime: EventRuntime | None = None
    provider: str | None = None
    model: str | None = None
    # Semantics.
    phase: EventPhase | None = None
    summary: str | None = None
    call_id: str | None = None
    parent_event_id: str | None = None
    duration_ms: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    visibility: EventVisibility = Field(default=EventVisibility.ADMIN_SAFE)
    # Provenance for summaries that are *not* raw model reasoning.
    source: str | None = Field(
        default=None,
        description="For reasoning.summary: 'provider' when the provider supplied a reasoning "
        "summary, 'runtime_summary' when derived from deterministic runtime state. "
        "Must be 'runtime_summary' (never 'provider') for template-derived summaries.",
    )

    def to_sse_data(self) -> dict[str, Any]:
        """Compact dict for SSE serialization (drops None context fields)."""
        payload = self.model_dump(exclude_none=True)
        # Always keep data even if empty so the UI can rely on the key.
        payload.setdefault("data", {})
        return payload


TERMINAL_TYPES = frozenset(
    {
        DebugEventType.RUN_COMPLETED.value,
        DebugEventType.RUN_CANCELLED.value,
        DebugEventType.RUN_TIMEOUT.value,
        DebugEventType.RUN_FAILED.value,
    }
)
