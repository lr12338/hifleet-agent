"""Responses API -> DebugEvent V1 adapter.

Two modes:
1. ``adapt_responses_event``: maps native Responses streaming events to V1.
   Used only when the provider actually streams Responses (probed, not assumed).
2. ``responses_step_*``: for the *real* current customer_ceshi runtime, which
   runs a synchronous ``responses.create()`` tool loop inside one graph node.
   These helpers emit real tool.started/tool.completed around each actual tool
   execution and a single answer.completed at the end (step stream, not token
   stream). They never split a synchronous result into fake token deltas.
"""
from __future__ import annotations

from typing import Any

from .contracts import DebugEventType, EventPhase, EventRuntime
from .emitter import DebugEmitter
from .redaction import redact_value


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# Native Responses event type -> handler. Unknown types fall through to
# raw_provider_event so the UI never misreads them as answers.
def adapt_responses_event(emitter: DebugEmitter, event: Any) -> list[Any]:
    emitted: list[Any] = []
    etype = _get(event, "type")
    if etype == "response.created":
        emitted.append(emitter.run_started(summary="Responses 运行已创建"))
    elif etype == "response.in_progress":
        emitted.append(emitter.phase_started(EventPhase.UNDERSTANDING, summary="Responses 处理中"))
    elif etype == "response.reasoning_summary_text.delta":
        delta = _get(event, "delta")
        if isinstance(delta, str) and delta:
            emitted.append(emitter.reasoning_summary(delta, source="provider"))
    elif etype == "response.output_text.delta":
        delta = _get(event, "delta")
        if isinstance(delta, str) and delta:
            emitted.append(emitter.answer_delta(delta))
    elif etype == "response.function_call_arguments.delta":
        call_id = _get(event, "item_id") or _get(event, "call_id")
        delta = _get(event, "delta")
        if call_id and isinstance(delta, str) and delta:
            emitted.append(emitter.tool_arguments_delta(call_id=call_id, delta=delta))
    elif etype == "response.output_item.added":
        item = _get(event, "item") or {}
        if _get(item, "type") == "function_call":
            call_id = _get(item, "call_id") or _get(item, "id")
            name = _get(item, "name") or "unknown"
            if call_id:
                emitted.append(emitter.tool_started(name=name, call_id=call_id, arguments_summary={}))
    elif etype == "response.output_item.done":
        item = _get(event, "item") or {}
        if _get(item, "type") == "function_call":
            call_id = _get(item, "call_id") or _get(item, "id")
            if call_id:
                emitted.append(
                    emitter.tool_completed(
                        call_id=call_id,
                        result_summary={"tool_name": _get(item, "name"), "arguments": redact_value(_get(item, "arguments"))},
                        status="completed",
                    )
                )
    elif etype == "response.completed":
        resp = _get(event, "response") or {}
        text = _get(resp, "output_text")
        emitted.append(emitter.answer_completed(full_text=text if isinstance(text, str) else None))
        emitted.append(emitter.run_completed(summary="Responses 运行完成"))
    elif etype == "response.failed":
        err = _get(event, "error") or {}
        emitted.append(emitter.run_failed(error=str(_get(err, "message") or "Responses failed")))
    elif etype == "response.incomplete":
        emitted.append(emitter.run_failed(error="Responses incomplete"))
    else:
        emitted.append(emitter.raw_provider_event(event if isinstance(event, dict) else {"raw": str(event)}, runtime=EventRuntime.RESPONSES))
    return emitted


# --- Synchronous step mode (current customer_ceshi reality) ---------------

def responses_step_tool_started(emitter: DebugEmitter, *, name: str, call_id: str, arguments: Any) -> Any:
    """Emit a real tool.started before executing a tool in the sync loop."""
    return emitter.tool_started(name=name, call_id=call_id, arguments_summary={"arguments": redact_value(arguments)})


def responses_step_tool_finished(
    emitter: DebugEmitter, *, name: str, call_id: str, result: Any, status: str = "completed", error: str | None = None
) -> Any:
    """Emit a real tool.completed/tool.failed after the tool actually returned."""
    if status == "failed":
        return emitter.tool_failed(call_id=call_id, error=error or "tool failed")
    return emitter.tool_completed(
        call_id=call_id, result_summary={"tool_name": name, "result": redact_value(result)}, status=status
    )


def responses_step_answer(emitter: DebugEmitter, *, full_text: str, observations: list[dict[str, Any]] | None = None) -> list[Any]:
    """Emit evidence + a single answer.completed for the sync step stream.

    Marks the run as a step stream (no provider token increments).
    """
    emitted: list[Any] = []
    if observations:
        emitted.append(
            emitter.evidence_summary(
                summary=f"共完成 {len(observations)} 次工具观察。",
                sufficient=True,
                data={"count": len(observations)},
            )
        )
    emitted.append(emitter.answer_started())
    emitted.append(emitter.answer_completed(full_text=full_text))
    return emitted


def responses_step_fallback(emitter: DebugEmitter, *, requested_runtime: str, effective_runtime: str, fallback_reason: str) -> Any:
    """Mark a Responses->Chat fallback honestly; never show Responses succeeded."""
    return emitter.route_selected(
        route="fallback",
        summary=f"Responses 不可用，回退到 {effective_runtime}",
        data={"requested_runtime": requested_runtime, "effective_runtime": effective_runtime, "fallback_reason": fallback_reason},
    )


__all__ = [
    "adapt_responses_event",
    "responses_step_tool_started",
    "responses_step_tool_finished",
    "responses_step_answer",
    "responses_step_fallback",
]
