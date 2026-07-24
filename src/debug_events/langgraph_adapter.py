"""LangGraph node-update -> DebugEvent V1 adapter for customer_support.

This replaces the fabricated trace in ``customer_support_stream_debug.py``. It
only reads *real* node state and never:
- special-cases attachment filenames,
- pre-fabricates tool_request / planned tool_response,
- disguises template sentences as model reasoning,
- marks a tool completed before it really returned.

Tool *execution* events (tool.started/completed with call_id+result) are NOT
emitted from node updates (which lack call_ids and results); they come from the
real :class:`ToolCallCallbackHandler` fed by LangGraph callbacks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import EventPhase
from .emitter import DebugEmitter
from .redaction import redact_value


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "input_text"}:
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return ""


def _final_answer(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        content = None
        role = ""
        if isinstance(msg, dict):
            role = str(msg.get("role") or msg.get("type") or "").lower()
            content = msg.get("content")
        else:
            role = str(getattr(msg, "type", getattr(msg, "role", ""))).lower()
            content = getattr(msg, "content", None)
        if role in {"assistant", "ai"}:
            return _text(content)
    return ""


@dataclass
class SupportCursor:
    started: bool = False
    route_emitted: bool = False
    answer_sent: bool = False
    seen_summary_phases: set[str] = field(default_factory=set)


def adapt_customer_support_update(
    emitter: DebugEmitter,
    node_name: str,
    state: dict[str, Any],
    cursor: SupportCursor,
) -> list[Any]:
    """Emit DebugEvents for one real LangGraph node update. Honest by construction."""
    emitted: list[Any] = []
    if not isinstance(state, dict):
        return emitted

    if node_name == "route":
        route = str(state.get("route", "") or "")
        cursor.started = True
        if route and not cursor.route_emitted:
            emitted.append(emitter.route_selected(route=route, data={"task_type": str(state.get("task_type", "") or "")}))
            cursor.route_emitted = True

    elif node_name == "delegate":
        cursor.started = True
        route = str(state.get("route", "") or "")
        task_type = str(state.get("task_type", "") or "")
        if route and not cursor.route_emitted:
            emitted.append(emitter.route_selected(route=route, data={"task_type": task_type}))
            cursor.route_emitted = True
        # Runtime summary (NOT model reasoning): describe the real route. Emit once.
        key = f"delegate:{route}:{task_type}"
        if key not in cursor.seen_summary_phases:
            cursor.seen_summary_phases.add(key)
            summary_parts = [f"路由：{route or 'knowledge'}"]
            if task_type:
                summary_parts.append(f"任务类型：{task_type}")
            attachments = state.get("attachments") or []
            if isinstance(attachments, list) and attachments:
                types = [str(a.get("type", "")) for a in attachments if isinstance(a, dict)]
                summary_parts.append(f"附件：{', '.join(t for t in types if t) or '未知'}")
            emitted.append(
                emitter.reasoning_summary("；".join(summary_parts), source="runtime_summary", phase=EventPhase.UNDERSTANDING)
            )
            # Note: route_trace records tool names that ran, but node updates
            # lack call_ids/results, so we do NOT emit tool.completed here.
            route_trace = state.get("route_trace") or {}
            seq = route_trace.get("tool_call_sequence") if isinstance(route_trace, dict) else None
            if isinstance(seq, list) and seq:
                names = [str(n) for n in seq if str(n).strip()]
                if names:
                    emitted.append(
                        emitter.evidence_summary(
                            summary=f"路由记录的工具：{', '.join(dict.fromkeys(names))}（执行详情见工具事件）",
                            data={"recorded_tools": list(dict.fromkeys(names))},
                        )
                    )

    elif node_name == "check":
        check = state.get("check_result") if isinstance(state.get("check_result"), dict) else {}
        if check:
            action = "post_guard_applied" if check.get("post_guard_applied") else "passed"
            summary = (
                f"已生成回答：{'是' if check.get('has_answer') else '否'}；"
                f"链接校验：{'通过' if check.get('links_ok', True) else '未通过'}；"
                f"安全兜底：{'已应用' if check.get('post_guard_applied') else '未应用'}"
            )
            emitted.append(emitter.guard_result(summary=summary, action=action, data={"check_result": redact_value(check)}))

    elif node_name in {"finalize", "fail"}:
        if not cursor.answer_sent:
            answer = _final_answer(list(state.get("messages", []) or []))
            if answer:
                cursor.answer_sent = True
                emitted.append(emitter.answer_started())
                emitted.append(emitter.answer_completed(full_text=answer))

    return emitted


def finalize_support_run(emitter: DebugEmitter, cursor: SupportCursor, *, status: str = "completed", error: str | None = None) -> list[Any]:
    """Emit the terminal run.* event if not already terminal."""
    emitted: list[Any] = []
    if emitter.is_terminal:
        return emitted
    if status == "completed":
        emitted.append(emitter.run_completed(summary="customer_support 运行完成"))
    elif status == "cancelled":
        emitted.append(emitter.run_cancelled())
    elif status == "timeout":
        emitted.append(emitter.run_timeout())
    else:
        emitted.append(emitter.run_failed(error=error or "customer_support 运行失败"))
    return emitted


class ToolCallCallbackHandler:
    """LangChain callback handler that emits REAL tool.started/completed events.

    Pass it via ``config={"callbacks": [handler]}`` to ``graph.astream``. It only
    observes; it never alters decisions. Uses ``run_id`` as the stable call_id.
    """

    def __init__(self, emitter: DebugEmitter) -> None:
        self._emitter = emitter
        self._names: dict[str, str] = {}
        self._args: dict[str, Any] = {}

    def _tool_name(self, serialized: Any, tool_str: str) -> str:
        if isinstance(serialized, dict):
            return str(serialized.get("name") or serialized.get("id") or "")
        return str(tool_str or "")

    def on_tool_start(self, serialized: Any, input_str: Any, *, run_id: Any = None, **_: Any) -> None:
        call_id = str(run_id or id(input_str))
        name = self._tool_name(serialized, str(input_str))
        self._names[call_id] = name
        self._args[call_id] = input_str
        try:
            self._emitter.tool_started(name=name or "unknown", call_id=call_id, arguments_summary={"input": redact_value(input_str)})
        except ValueError:
            pass

    def on_tool_end(self, output: Any, *, run_id: Any = None, **_: Any) -> None:
        call_id = str(run_id or "")
        name = self._names.get(call_id, "unknown")
        try:
            self._emitter.tool_completed(call_id=call_id, result_summary={"tool_name": name, "result": redact_value(output)}, status="completed")
        except (ValueError, KeyError):
            pass

    def on_tool_error(self, error: Any, *, run_id: Any = None, **_: Any) -> None:
        call_id = str(run_id or "")
        try:
            self._emitter.tool_failed(call_id=call_id, error=str(error))
        except (ValueError, KeyError):
            pass


__all__ = ["SupportCursor", "adapt_customer_support_update", "finalize_support_run", "ToolCallCallbackHandler"]
