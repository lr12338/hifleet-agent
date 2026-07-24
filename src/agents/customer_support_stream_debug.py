"""Customer-support explainable streaming events for admin chat debugging.

Honest by construction: events describe *real* node state only.

Removed (per audit):
- filename special-casing (``01_query`` -> 安全水域浮标, ``03_query``/圈圈 -> 锚地范围圈);
- pre-fabricated ``tool_request`` / ``planned tool_response`` issued before any
  tool executed;
- template step-by-step sentences disguised as model reasoning.

Runtime-derived summaries now carry ``source="runtime_summary"`` and state only
facts (route, task type, attachment count, recorded tool names). Real tool
*execution* events (tool.started/completed with call_id + result) are emitted by
``debug_events.ToolCallCallbackHandler`` from LangGraph callbacks, never guessed
from node state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from agents.customer_support_guard import sanitize_customer_output


def _event(event_type: str, text: str = "", **extra: Any) -> dict[str, Any]:
    payload = {"type": event_type, **extra}
    if text:
        payload["text"] = text
    return payload


@dataclass
class DebugRuntimeCursor:
    started: bool = False
    ended: bool = False
    route: str = ""
    task_type: str = ""
    seen_runtime_summary: set[str] = field(default_factory=set)
    answer_sent: bool = False


def _extract_final_answer(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            return sanitize_customer_output(str(msg.content or ""))
        if isinstance(msg, dict):
            role = str(msg.get("role") or msg.get("type") or "").lower()
            if role in {"assistant", "ai"}:
                return sanitize_customer_output(str(msg.get("content", "") or ""))
    return ""


def _recorded_tools(state: dict[str, Any]) -> list[str]:
    route_trace = state.get("route_trace") or {}
    seq = route_trace.get("tool_call_sequence") if isinstance(route_trace, dict) else None
    if not isinstance(seq, list):
        return []
    return [str(n) for n in seq if str(n).strip()]


def _events_from_delegate_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    route = str(state.get("route", "") or cursor.route)
    task_type = str(state.get("task_type", "") or cursor.task_type)
    cursor.route = route
    cursor.task_type = task_type
    if not cursor.started:
        events.append(_event("message_start", "开始处理 customer_support 调试流。", route=route, task_type=task_type))
        cursor.started = True
    key = f"delegate:{route}:{task_type}"
    if key not in cursor.seen_runtime_summary:
        cursor.seen_runtime_summary.add(key)
        parts = [f"路由：{route or 'knowledge'}"]
        if task_type:
            parts.append(f"任务类型：{task_type}")
        attachments = list(state.get("attachments", []) or [])
        if attachments:
            types = [str(a.get("type", "")) for a in attachments if isinstance(a, dict)]
            parts.append(f"附件：{', '.join(t for t in types if t) or '未知'}")
        tools = _recorded_tools(state)
        if tools:
            parts.append(f"路由记录已执行工具：{', '.join(dict.fromkeys(tools))}")
        events.append(
            _event(
                "thinking",
                sanitize_customer_output("运行时摘要：" + "；".join(parts) + "。由标准客服 Agent 自主决策工具与回复。"),
                phase="standard_agent",
                source="runtime_summary",
            )
        )
    return events


def _events_from_check_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    check = dict(state.get("check_result", {}) or {})
    if check:
        text = "运行时摘要：后置内容质检。"
        text += f"已生成回答：{'是' if check.get('has_answer') else '否'}；"
        text += f"链接校验：{'通过' if check.get('links_ok', True) else '未通过'}；"
        if check.get("post_guard_applied"):
            text += "已应用安全兜底。"
        else:
            text += "输出已通过脱敏和客服收口。"
        events.append(_event("thinking", sanitize_customer_output(text), phase="post_guard", source="runtime_summary"))
    return events


def _events_from_terminal_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    if cursor.answer_sent:
        return []
    answer = _extract_final_answer(list(state.get("messages", []) or []))
    if not answer:
        return []
    cursor.answer_sent = True
    cursor.ended = True
    return [_event("answer", answer), _event("message_end", "customer_support 调试流结束。")]


def build_customer_support_debug_events_from_update(
    update: dict[str, Any], cursor: DebugRuntimeCursor | None = None
) -> list[dict[str, Any]]:
    cursor = cursor or DebugRuntimeCursor()
    events: list[dict[str, Any]] = []
    for node_name, state in (update or {}).items():
        if not isinstance(state, dict):
            continue
        if node_name == "route":
            if not cursor.started:
                events.append(_event("message_start", "开始处理 customer_support 调试流。"))
                cursor.started = True
        elif node_name == "delegate":
            events.extend(_events_from_delegate_state(state, cursor))
        elif node_name == "check":
            events.extend(_events_from_check_state(state, cursor))
        elif node_name in {"finalize", "fail"}:
            events.extend(_events_from_terminal_state(state, cursor))
    return events
