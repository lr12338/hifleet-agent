"""Unified DebugEvent V1 protocol for the dialogue test workbench.

This package normalizes Chat Completions and Responses API runtime signals into
a single, safe event model so the admin UI never has to interpret raw provider
packets. Events are only emitted from real runtime state/callbacks; nothing here
fabricates model reasoning or tool execution.
"""
from __future__ import annotations

import json
from typing import Iterable

from .aggregator import DebugAggregator
from .chat_adapter import adapt_chat_chunk, adapt_chat_final_message, complete_chat_tool
from .contracts import (
    CUSTOMER_VISIBLE_TYPES,
    DEBUG_EVENT_SCHEMA_VERSION,
    TERMINAL_TYPES,
    DebugEvent,
    DebugEventType,
    EventPhase,
    EventRuntime,
    EventVisibility,
)
from .emitter import DebugEmitter
from .langgraph_adapter import SupportCursor, ToolCallCallbackHandler, adapt_customer_support_update, finalize_support_run
from .redaction import redact_event_data, redact_headers, redact_value, sanitize_url, truncate
from .responses_adapter import (
    adapt_responses_event,
    responses_step_answer,
    responses_step_fallback,
    responses_step_tool_finished,
    responses_step_tool_started,
)


def encode_sse(event: DebugEvent, *, event_name: str = "debug") -> bytes:
    """Serialize a DebugEvent as an SSE frame: ``event: debug\\ndata: {json}\\n\\n``."""
    payload = json.dumps(event.to_sse_data(), ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")


def encode_sse_lines(event: DebugEvent, *, event_name: str = "debug") -> bytes:
    """SSE frame using CRLF-friendly \\n\\n delimiter (parser handles both)."""
    payload = json.dumps(event.to_sse_data(), ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")


def iter_debug_sse(events: Iterable[DebugEvent], *, event_name: str = "debug") -> Iterable[bytes]:
    for ev in events:
        yield encode_sse(ev, event_name=event_name)


__all__ = [
    "DEBUG_EVENT_SCHEMA_VERSION",
    "CUSTOMER_VISIBLE_TYPES",
    "TERMINAL_TYPES",
    "DebugEvent",
    "DebugEventType",
    "EventPhase",
    "EventRuntime",
    "EventVisibility",
    "DebugEmitter",
    "DebugAggregator",
    "SupportCursor",
    "ToolCallCallbackHandler",
    "adapt_customer_support_update",
    "finalize_support_run",
    "adapt_chat_chunk",
    "adapt_chat_final_message",
    "complete_chat_tool",
    "adapt_responses_event",
    "responses_step_tool_started",
    "responses_step_tool_finished",
    "responses_step_answer",
    "responses_step_fallback",
    "redact_event_data",
    "redact_headers",
    "redact_value",
    "sanitize_url",
    "truncate",
    "encode_sse",
    "encode_sse_lines",
    "iter_debug_sse",
]
