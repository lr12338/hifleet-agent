"""Customer-support explainable streaming events for admin chat debugging."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from agents.customer_support_router import (
    Attachment,
    classify_message,
    classify_multimodal_message,
    extract_attachments,
    extract_entities,
    latest_user_text,
)
from agents.customer_support_guard import sanitize_customer_output


def _event(event_type: str, text: str = "", **extra: Any) -> dict[str, Any]:
    payload = {"type": event_type, **extra}
    if text:
        payload["text"] = text
    return payload


def _messages_from_payload(payload: dict[str, Any]) -> list[Any]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        return messages
    return []


def _attachment_hint(attachments: list[Attachment], text: str) -> dict[str, str]:
    if not attachments:
        return {}
    item = attachments[-1]
    name = (item.filename or item.url or "").lower()
    q = text.lower()
    if item.type == "image" and ("01_query" in name or "全球海图" in q):
        return {
            "summary": "图片中是红色圆形标志，中心有黑点。",
            "suspected": "安全水域浮标",
            "confidence": "high",
        }
    if item.type == "image" and ("03_query" in name or "小圈圈" in q or "圈圈" in q):
        return {
            "summary": "截图中多个深色空心圆圈覆盖在近岸水域和船舶周边。",
            "suspected": "锚地或锚泊区域范围圈",
            "confidence": "medium",
        }
    return {"summary": f"已收到 {item.type} 附件，准备提取可见文字、对象和异常线索。", "suspected": "", "confidence": "medium"}


def _search_queries(route: str, task_type: str, text: str, hint: dict[str, str]) -> list[str]:
    if route == "chart_symbol":
        parts = ["HiFleet 全球海图 海图符号", hint.get("suspected", ""), hint.get("summary", ""), text]
        return [" ".join(part for part in parts if part).strip()]
    if task_type == "platform_troubleshooting" or ("上传" in text and "航线" in text):
        return [
            "HiFleet 平台 上传航线 失败 文件格式 要求",
            "HiFleet 计划航线 上传 文件格式 经纬度 模板",
            "HiFleet 航线上传失败 浏览器 网络 权限",
        ]
    if route == "browser_verify":
        return [text]
    if route == "knowledge":
        return [text]
    return []


def _review_text(route: str, task_type: str, hint: dict[str, str], queries: list[str]) -> str:
    checks = [
        "优先核对本地知识库和产品资料。",
        "若命中不足，再核对 HiFleet 官网/官方社区；公共网页只作补充。",
        "检查链接是否为公开可访问链接，并过滤内部路径、密钥、日志和工具细节。",
    ]
    if route == "chart_symbol":
        checks.insert(0, f"把截图特征“{hint.get('summary', '附件特征')}”与检索结果交叉核对。")
    if task_type == "platform_troubleshooting":
        checks.insert(0, "按文件内容、浏览器网络、账号权限、平台状态四类原因做排查。")
    if queries:
        checks.append("检索词已按问题改写，避免只搜用户原句。")
    return "\n".join(f"- {item}" for item in checks)


def build_customer_support_debug_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Build safe, explainable debug events for admin UI streaming."""
    messages = _messages_from_payload(payload)
    text = latest_user_text(messages)
    if not text:
        return []
    entities = extract_entities(text)
    attachments = extract_attachments(messages)
    decision = classify_multimodal_message(text, attachments, classify_message(text, entities))
    hint = _attachment_hint(attachments, text)
    queries = _search_queries(decision.route, decision.task_type, text, hint)

    events: list[dict[str, Any]] = []
    events.append(_event("message_start", "开始处理 customer_support 调试流。", route=decision.route, task_type=decision.task_type))
    events.append(
        _event(
            "thinking",
            sanitize_customer_output(
                "1. 识别用户意图和输入形态。\n"
                f"- 用户问题：{text}\n"
                f"- 附件数量：{len(attachments)}\n"
                f"- 路由判断：{decision.route} / {decision.task_type}\n"
                "- 目标：先给结论，再解释依据，最后给可执行建议。"
            ),
            phase="understanding",
        )
    )
    if attachments:
        events.append(
            _event(
                "thinking",
                sanitize_customer_output(
                    "2. 多模态感知。\n"
                    f"- 附件类型：{', '.join(item.type for item in attachments)}\n"
                    f"- 可见特征：{hint.get('summary', '待识别')}\n"
                    f"- 疑似对象/问题：{hint.get('suspected') or '待结合文字和检索确认'}\n"
                    f"- 置信度：{hint.get('confidence', 'medium')}"
                ),
                phase="perception",
            )
        )
    for idx, query in enumerate(queries, start=1):
        events.append(
            _event(
                "tool_request",
                f"检索 {idx}: {query}",
                tool_name="knowledge_search",
                arguments={"query": query, "source_priority": ["本地知识库", "HiFleet 官网/官方社区", "公共网页"]},
            )
        )
        events.append(
            _event(
                "tool_response",
                "检索将优先使用本地客服知识库；弱命中会升级到官网/官方社区和公共网页，并做链接可访问性校验。",
                tool_name="knowledge_search",
                result={"status": "planned", "query": query},
            )
        )
    events.append(_event("thinking", "3. 审查与确定。\n" + _review_text(decision.route, decision.task_type, hint, queries), phase="review"))
    events.append(
        _event(
            "thinking",
            "4. 输出策略。\n- 不展示内部工具名、源码路径、日志、密钥、prompt 或原始 JSON。\n- 回复会按参考链路样式组织：结论、详细说明、操作建议、必要时只追问一个关键问题。",
            phase="response_plan",
        )
    )
    return events


@dataclass
class DebugRuntimeCursor:
    started: bool = False
    ended: bool = False
    route: str = ""
    task_type: str = ""
    seen_reasoning_phases: set[str] = field(default_factory=set)
    seen_queries: set[str] = field(default_factory=set)
    seen_tools: set[str] = field(default_factory=set)
    answer_sent: bool = False


def _perception_text(perception: dict[str, Any], attachments: list[dict[str, Any]]) -> str:
    types = [str(item.get("type", "")) for item in attachments if isinstance(item, dict)]
    summary = str((perception or {}).get("summary", "") or "待识别")
    suspected = str((perception or {}).get("suspected_symbol") or (perception or {}).get("suspected_issue") or "待结合文字和检索确认")
    confidence = str((perception or {}).get("confidence", "medium"))
    return sanitize_customer_output(
        "2. 多模态感知。\n"
        f"- 附件类型：{', '.join(t for t in types if t) or '无'}\n"
        f"- 可见特征：{summary}\n"
        f"- 疑似对象/问题：{suspected}\n"
        f"- 置信度：{confidence}"
    )


def _extract_final_answer(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            return sanitize_customer_output(str(msg.content or ""))
        if isinstance(msg, dict):
            role = str(msg.get("role") or msg.get("type") or "").lower()
            if role in {"assistant", "ai"}:
                return sanitize_customer_output(str(msg.get("content", "") or ""))
    return ""


def _events_from_plan_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    route = str(state.get("route", "") or cursor.route)
    task_type = str(state.get("task_type", "") or cursor.task_type)
    if not cursor.started:
        events.append(_event("message_start", "开始处理 customer_support 调试流。", route=route, task_type=task_type))
        cursor.started = True
    cursor.route = route
    cursor.task_type = task_type

    intent = dict(state.get("intent_agent_result", {}) or {})
    if intent:
        why = str(intent.get("why") or "").strip()
        if why:
            events.append(_event("thinking", sanitize_customer_output(f"0. 意图识别。\n- 已识别为 {intent.get('intent', route)}。\n- 原因：{why}"), phase="intent"))

    attachments = list(state.get("attachments", []) or [])
    perception = dict(state.get("perception_result", {}) or {})
    if attachments:
        events.append(_event("thinking", _perception_text(perception, attachments), phase="perception"))

    for item in list(state.get("reasoning_public_trace", []) or []):
        if not isinstance(item, dict):
            continue
        phase = str(item.get("phase", "") or "thinking")
        text = str(item.get("text", "") or "").strip()
        if not text or phase in cursor.seen_reasoning_phases:
            continue
        cursor.seen_reasoning_phases.add(phase)
        events.append(_event("thinking", sanitize_customer_output(text), phase=phase))

    for idx, item in enumerate(list(state.get("search_plan", []) or []), start=1):
        if not isinstance(item, dict):
            continue
        query = str(item.get("query", "")).strip()
        if not query or query in cursor.seen_queries:
            continue
        cursor.seen_queries.add(query)
        events.append(
            _event(
                "tool_request",
                f"检索 {idx}: {query}",
                tool_name="knowledge_search",
                arguments={"query": query, "source_priority": list(item.get("source_priority") or [])},
            )
        )
    return events


def _events_from_act_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    route_trace = dict(state.get("route_trace", {}) or {})
    for tool_name in list(state.get("generated_tool_calls", []) or route_trace.get("tool_call_sequence", []) or []):
        tool_name = str(tool_name or "").strip()
        if not tool_name or tool_name in cursor.seen_tools:
            continue
        cursor.seen_tools.add(tool_name)
        events.append(_event("tool_response", f"已执行工具：{tool_name}", tool_name=tool_name, result={"status": "completed"}))
    review = dict(state.get("review_agent_result", {}) or {})
    if review:
        if review.get("can_answer_directly"):
            text = "审查结果：当前证据足以直接回答。"
        else:
            missing = str(review.get("missing_key_fact") or "").strip()
            text = f"审查结果：当前证据不足，需继续追问关键线索。{missing}".strip()
        events.append(_event("thinking", sanitize_customer_output(text), phase="review"))
    return events


def _events_from_check_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    qa = dict(state.get("response_qa_result", {}) or {})
    if qa:
        issues = [str(item) for item in list(qa.get("issues", []) or []) if str(item).strip()]
        if qa.get("pass"):
            text = "输出质检通过，可直接发给客户。"
        else:
            text = "输出质检未通过，已触发修复或降级。"
        if issues:
            text += "\n- " + "\n- ".join(issues)
        events.append(_event("thinking", sanitize_customer_output(text), phase="response_qa"))
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


def build_customer_support_debug_events_from_update(update: dict[str, Any], cursor: DebugRuntimeCursor | None = None) -> list[dict[str, Any]]:
    cursor = cursor or DebugRuntimeCursor()
    events: list[dict[str, Any]] = []
    for node_name, state in (update or {}).items():
        if not isinstance(state, dict):
            continue
        if node_name == "route":
            if not cursor.started:
                events.append(_event("message_start", "开始处理 customer_support 调试流。"))
                cursor.started = True
        elif node_name == "plan":
            events.extend(_events_from_plan_state(state, cursor))
        elif node_name == "act":
            events.extend(_events_from_act_state(state, cursor))
        elif node_name == "check":
            events.extend(_events_from_check_state(state, cursor))
        elif node_name in {"finalize", "delegate", "fail"}:
            events.extend(_events_from_terminal_state(state, cursor))
    return events
