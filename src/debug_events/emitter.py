"""DebugEmitter: builds DebugEvent V1 events with monotonic seq, run_id and
call_id pairing for tool started/completed/failed.

The emitter is the single source of ``seq``/``event_id``/timing truth for a run.
It never fabricates content: it only wraps real signals provided by adapters.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Iterable

from .contracts import (
    CUSTOMER_VISIBLE_TYPES,
    DEBUG_EVENT_SCHEMA_VERSION,
    DebugEvent,
    DebugEventType,
    EventPhase,
    EventRuntime,
    EventVisibility,
)
from .redaction import redact_event_data


def _new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


class DebugEmitter:
    def __init__(
        self,
        run_id: str,
        *,
        session_id: str | None = None,
        agent_profile: str | None = None,
        endpoint: str | None = None,
        runtime: EventRuntime | None = None,
        provider: str | None = None,
        model: str | None = None,
        clock: Any = None,
    ) -> None:
        self.run_id = run_id
        self._ctx: dict[str, Any] = {
            "session_id": session_id,
            "agent_profile": agent_profile,
            "endpoint": endpoint,
            "runtime": runtime,
            "provider": provider,
            "model": model,
        }
        self._clock = clock or time.perf_counter
        self._started_perf = self._clock()
        self._first_event_perf: float | None = None
        self._first_token_perf: float | None = None
        self._seq = -1
        self._pending_tools: dict[str, dict[str, Any]] = {}
        self._tool_count = 0
        self._model_calls = 0
        self._kb_count = 0
        self._web_count = 0
        self._media_count = 0
        self._answer_parts: list[str] = []
        self._terminal = False

    # -- internal -----------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _elapsed_ms(self, since: float | None = None) -> int:
        return max(0, int((self._clock() - (since if since is not None else self._started_perf)) * 1000))

    def _emit(
        self,
        type_: DebugEventType,
        *,
        phase: EventPhase | None = None,
        summary: str | None = None,
        call_id: str | None = None,
        parent_event_id: str | None = None,
        duration_ms: int | None = None,
        data: dict[str, Any] | None = None,
        visibility: EventVisibility = EventVisibility.ADMIN_SAFE,
        source: str | None = None,
        runtime: EventRuntime | None = None,
    ) -> DebugEvent:
        if self._terminal and type_ not in (DebugEventType.HEARTBEAT,):
            # After a terminal run.* event, only heartbeats are allowed.
            raise RuntimeError(f"Cannot emit {type_} after terminal run event")
        seq = self._next_seq()
        event_id = _new_event_id()
        if self._first_event_perf is None:
            self._first_event_perf = self._clock()
        ctx = dict(self._ctx)
        if runtime is not None:
            ctx["runtime"] = runtime
        event = DebugEvent(
            event_id=event_id,
            seq=seq,
            run_id=self.run_id,
            type=type_,
            phase=phase,
            summary=summary,
            call_id=call_id,
            parent_event_id=parent_event_id,
            duration_ms=duration_ms,
            data=redact_event_data(data or {}),
            visibility=visibility,
            source=source,
            **{k: v for k, v in ctx.items() if v is not None},
        )
        if type_ in (
            DebugEventType.RUN_COMPLETED,
            DebugEventType.RUN_CANCELLED,
            DebugEventType.RUN_TIMEOUT,
            DebugEventType.RUN_FAILED,
        ):
            self._terminal = True
        return event

    # -- lifecycle ----------------------------------------------------------
    def run_started(self, *, summary: str | None = None, data: dict[str, Any] | None = None) -> DebugEvent:
        return self._emit(DebugEventType.RUN_STARTED, phase=EventPhase.INTAKE, summary=summary, data=data)

    def input_normalized(self, *, summary: str | None = None, data: dict[str, Any] | None = None) -> DebugEvent:
        return self._emit(DebugEventType.INPUT_NORMALIZED, phase=EventPhase.INTAKE, summary=summary, data=data)

    def route_selected(self, *, route: str | None = None, summary: str | None = None, data: dict[str, Any] | None = None) -> DebugEvent:
        payload = dict(data or {})
        if route:
            payload.setdefault("route", route)
        return self._emit(DebugEventType.ROUTE_SELECTED, phase=EventPhase.ROUTING, summary=summary or route, data=payload)

    def phase_started(self, phase: EventPhase, *, summary: str | None = None) -> DebugEvent:
        return self._emit(DebugEventType.PHASE_STARTED, phase=phase, summary=summary)

    def phase_completed(self, phase: EventPhase, *, summary: str | None = None, duration_ms: int | None = None) -> DebugEvent:
        return self._emit(DebugEventType.PHASE_COMPLETED, phase=phase, summary=summary, duration_ms=duration_ms)

    def reasoning_summary(self, text: str, *, source: str = "runtime_summary", phase: EventPhase | None = None) -> DebugEvent:
        """Surface a reasoning summary. ``source`` MUST be 'runtime_summary' for
        deterministic/template-derived text and 'provider' only when the provider
        explicitly returned a reasoning summary delta."""
        return self._emit(
            DebugEventType.REASONING_SUMMARY,
            phase=phase or EventPhase.UNDERSTANDING,
            summary=text,
            source=source,
            visibility=EventVisibility.ADMIN_SAFE,
        )

    # -- tools --------------------------------------------------------------
    def tool_started(self, *, name: str, call_id: str, arguments_summary: dict[str, Any] | None = None) -> DebugEvent:
        if call_id in self._pending_tools:
            raise ValueError(f"tool started twice for call_id={call_id}")
        self._tool_count += 1
        if name == "local_kb_search":
            self._kb_count += 1
        elif name == "web_search":
            self._web_count += 1
        elif name == "inspect_media":
            self._media_count += 1
        data = {"tool_name": name, "arguments": arguments_summary or {}}
        event = self._emit(
            DebugEventType.TOOL_STARTED,
            phase=EventPhase.TOOL_EXECUTION,
            call_id=call_id,
            summary=f"调用工具：{name}",
            data=data,
        )
        self._pending_tools[call_id] = {"name": name, "started_perf": self._clock(), "started_event_id": event.event_id}
        return event

    def tool_arguments_delta(self, *, call_id: str, delta: str) -> DebugEvent:
        return self._emit(
            DebugEventType.TOOL_ARGUMENTS_DELTA,
            phase=EventPhase.TOOL_EXECUTION,
            call_id=call_id,
            data={"delta": delta},
        )

    def tool_completed(self, *, call_id: str, result_summary: dict[str, Any] | None = None, status: str = "completed") -> DebugEvent:
        pending = self._pending_tools.pop(call_id, None)
        duration_ms = self._elapsed_ms(pending["started_perf"]) if pending else None
        parent = pending["started_event_id"] if pending and "started_event_id" in pending else None
        data = {"tool_name": pending["name"] if pending else None, "result": result_summary or {}, "status": status}
        return self._emit(
            DebugEventType.TOOL_COMPLETED,
            phase=EventPhase.TOOL_EXECUTION,
            call_id=call_id,
            parent_event_id=parent,
            duration_ms=duration_ms,
            summary=f"工具完成：{pending['name']}" if pending else "工具完成",
            data=data,
        )

    def tool_failed(self, *, call_id: str, error: str) -> DebugEvent:
        pending = self._pending_tools.pop(call_id, None)
        duration_ms = self._elapsed_ms(pending["started_perf"]) if pending else None
        data = {"tool_name": pending["name"] if pending else None, "error": error}
        return self._emit(
            DebugEventType.TOOL_FAILED,
            phase=EventPhase.TOOL_EXECUTION,
            call_id=call_id,
            duration_ms=duration_ms,
            summary=f"工具失败：{pending['name']}" if pending else "工具失败",
            data=data,
        )

    # -- evidence / guard ---------------------------------------------------
    def evidence_summary(self, *, summary: str, sufficient: bool | None = None, data: dict[str, Any] | None = None) -> DebugEvent:
        payload = dict(data or {})
        if sufficient is not None:
            payload.setdefault("sufficient", sufficient)
        return self._emit(DebugEventType.EVIDENCE_SUMMARY, phase=EventPhase.EVIDENCE_RETRIEVAL, summary=summary, data=payload)

    def guard_result(self, *, summary: str, action: str | None = None, data: dict[str, Any] | None = None) -> DebugEvent:
        payload = dict(data or {})
        if action:
            payload.setdefault("action", action)
        return self._emit(DebugEventType.GUARD_RESULT, phase=EventPhase.GUARD, summary=summary, data=payload)

    # -- answer -------------------------------------------------------------
    def answer_started(self) -> DebugEvent:
        return self._emit(DebugEventType.ANSWER_STARTED, phase=EventPhase.RESPONSE_SYNTHESIS, summary="开始生成最终回答")

    def answer_delta(self, text: str) -> DebugEvent:
        if self._first_token_perf is None:
            self._first_token_perf = self._clock()
        self._answer_parts.append(text)
        return self._emit(DebugEventType.ANSWER_DELTA, phase=EventPhase.RESPONSE_SYNTHESIS, data={"delta": text})

    def answer_completed(self, *, full_text: str | None = None) -> DebugEvent:
        text = full_text if full_text is not None else "".join(self._answer_parts)
        from .redaction import _hash_preview  # local import to avoid cycle in docs

        return self._emit(
            DebugEventType.ANSWER_COMPLETED,
            phase=EventPhase.RESPONSE_SYNTHESIS,
            summary="最终回答已生成",
            data={"answer": text, "length": len(text), "hash": _hash_preview(text)},
        )

    # -- terminal -----------------------------------------------------------
    def _run_metrics(self) -> dict[str, Any]:
        return {
            "total_duration_ms": self._elapsed_ms(),
            "first_event_ms": self._elapsed_ms(self._first_event_perf) if self._first_event_perf else None,
            "first_token_ms": self._elapsed_ms(self._first_token_perf) if self._first_token_perf else None,
            "tool_calls": self._tool_count,
            "model_calls": self._model_calls,
            "knowledge_base_calls": self._kb_count,
            "web_search_calls": self._web_count,
            "media_reads": self._media_count,
            "unpaired_tools": list(self._pending_tools.keys()),
        }

    def run_completed(self, *, summary: str | None = None, data: dict[str, Any] | None = None) -> DebugEvent:
        payload = dict(data or {})
        payload.setdefault("metrics", self._run_metrics())
        return self._emit(DebugEventType.RUN_COMPLETED, phase=EventPhase.FINALIZATION, summary=summary or "运行完成", data=payload)

    def run_cancelled(self, *, summary: str | None = None) -> DebugEvent:
        return self._emit(DebugEventType.RUN_CANCELLED, phase=EventPhase.FINALIZATION, summary=summary or "运行已取消", data=self._run_metrics())

    def run_timeout(self, *, summary: str | None = None) -> DebugEvent:
        return self._emit(DebugEventType.RUN_TIMEOUT, phase=EventPhase.FINALIZATION, summary=summary or "运行超时", data=self._run_metrics())

    def run_failed(self, *, error: str, summary: str | None = None) -> DebugEvent:
        return self._emit(DebugEventType.RUN_FAILED, phase=EventPhase.FINALIZATION, summary=summary or "运行失败", data={"error": error, **self._run_metrics()})

    def heartbeat(self) -> DebugEvent:
        return self._emit(DebugEventType.HEARTBEAT, data={"elapsed_ms": self._elapsed_ms()})

    def raw_provider_event(self, payload: dict[str, Any], *, runtime: EventRuntime | None = None) -> DebugEvent:
        """Unknown provider events land here and must NOT be interpreted as answers."""
        return self._emit(
            DebugEventType.RAW_PROVIDER_EVENT,
            runtime=runtime,
            visibility=EventVisibility.INTERNAL_DEBUG,
            data={"raw": payload},
        )

    def record_model_call(self) -> None:
        self._model_calls += 1

    @property
    def is_terminal(self) -> bool:
        return self._terminal

    def is_customer_visible(self, event: DebugEvent) -> bool:
        return event.type in CUSTOMER_VISIBLE_TYPES or event.type == DebugEventType.HEARTBEAT.value
