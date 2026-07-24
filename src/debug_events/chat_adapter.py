"""Chat Completions -> DebugEvent V1 adapter.

Converts real Chat Completions streaming chunks (and LangChain AIMessageChunk
shapes) into normalized events. It only reads fields that actually exist on a
chunk; it never invents tool calls or reasoning.
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


def _delta(chunk: Any) -> Any:
    return _get(chunk, "delta", _get(chunk, "choices", None))


def _first_choice_delta(chunk: Any) -> Any:
    choices = _get(chunk, "choices")
    if isinstance(choices, list) and choices:
        return _get(choices[0], "delta")
    return _get(chunk, "delta")


def adapt_chat_chunk(
    emitter: DebugEmitter,
    chunk: Any,
    *,
    tool_call_index_to_id: dict[int, str] | None = None,
) -> list[Any]:
    """Emit DebugEvents for one Chat Completions streaming chunk.

    Returns the emitted events. ``tool_call_index_to_id`` remembers the call_id
    assigned when a tool call first appears so subsequent argument deltas pair.
    """
    tool_call_index_to_id = tool_call_index_to_id if tool_call_index_to_id is not None else {}
    emitted: list[Any] = []
    delta = _first_choice_delta(chunk)
    if delta is None:
        return emitted

    # Reasoning content (provider-supplied only).
    reasoning = _get(delta, "reasoning_content") or _get(delta, "reasoning")
    if isinstance(reasoning, str) and reasoning:
        emitted.append(emitter.reasoning_summary(reasoning, source="provider"))

    content = _get(delta, "content")
    if isinstance(content, str) and content:
        emitted.append(emitter.answer_delta(content))

    tool_calls = _get(delta, "tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            index = _get(tc, "index", 0)
            call_id = _get(tc, "id")
            function = _get(tc, "function") or {}
            name = _get(function, "name")
            args_delta = _get(function, "arguments")
            if call_id and index not in tool_call_index_to_id:
                tool_call_index_to_id[index] = call_id
                emitted.append(
                    emitter.tool_started(
                        name=name or "unknown",
                        call_id=call_id,
                        arguments_summary={"index": index},
                    )
                )
            cid = tool_call_index_to_id.get(index) or call_id
            if cid and isinstance(args_delta, str) and args_delta:
                emitted.append(emitter.tool_arguments_delta(call_id=cid, delta=args_delta))
    return emitted


def adapt_chat_final_message(emitter: DebugEmitter, message: Any) -> list[Any]:
    """Emit tool.started/completed for a fully-formed AIMessage with tool_calls.

    Used when the Chat path runs in non-streaming function-calling mode: the
    model returns complete tool calls at once. Each call is real, so we emit a
    started+completed pair with the parsed arguments.
    """
    emitted: list[Any] = []
    tool_calls = _get(message, "tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            call_id = _get(tc, "id") or f"call_{len(emitted)}"
            name = _get(_get(tc, "function") or {}, "name") or "unknown"
            args = _get(_get(tc, "function") or {}, "arguments")
            emitted.append(emitter.tool_started(name=name, call_id=call_id, arguments_summary={"arguments": redact_value(args)}))
    return emitted


def complete_chat_tool(emitter: DebugEmitter, *, call_id: str, name: str, result: Any, status: str = "completed") -> Any:
    """Emit tool.completed for a tool result that really executed."""
    return emitter.tool_completed(call_id=call_id, result_summary={"tool_name": name, "result": redact_value(result)}, status=status)


__all__ = ["adapt_chat_chunk", "adapt_chat_final_message", "complete_chat_tool"]
