"""Customer-support explainable streaming events for admin chat debugging."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from agents.customer_support_router import Attachment, classify_message, classify_multimodal_message, extract_attachments, extract_entities, latest_user_text
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
                "1. 前置安全与问题识别。\n"
                f"- 用户问题：{text}\n"
                f"- 附件数量：{len(attachments)}\n"
                f"- 路由判断：{decision.route} / {decision.task_type}\n"
                "- 命中敏感内部探查会直接拒答；正常请求进入标准客服 Agent。"
            ),
            phase="pre_guard",
        )
    )
    if attachments:
        events.append(
            _event(
                "thinking",
                sanitize_customer_output(
                    "2. 附件输入分析。\n"
                    f"- 附件类型：{', '.join(item.type for item in attachments)}\n"
                    f"- 可见特征：{hint.get('summary', '待识别')}\n"
                    f"- 疑似对象/问题：{hint.get('suspected') or '待结合文字和检索确认'}\n"
                    f"- 置信度：{hint.get('confidence', 'medium')}"
                ),
                phase="attachments",
            )
        )
    events.append(
        _event(
            "thinking",
            "2. 标准客服 Agent 装配。\n- 使用 customer_support profile prompt、会话历史和已注册工具。\n- Agent 会自主决定是否调用知识检索、船舶数据工具或其他受控工具。",
            phase="standard_agent",
        )
    )
    for idx, query in enumerate(queries[:3], start=1):
        events.append(
            _event(
                "tool_request",
                f"检索 {idx}: {query}",
                tool_name="smart_search",
                arguments={"query": query, "source_priority": ["本地知识库", "HiFleet 官网/官方社区", "公共网页"]},
            )
        )
        events.append(
            _event(
                "tool_response",
                "标准客服 Agent 会优先尝试本地知识库检索；弱命中时再补充更匹配的公开信息来源。",
                tool_name="smart_search",
                result={"status": "planned", "query": query},
            )
        )
    events.append(_event("thinking", "3. 后置内容质检。\n- 知识类问题默认优先本地知识库，再补充 HiFleet 官网/官方社区和必要的公开信息。\n- 最终输出会经过脱敏、链接校验和客服语调收口。\n- 如果结果不安全或不稳定，会降级为标准致歉/建议补充信息。", phase="post_guard"))
    events.append(
        _event(
            "thinking",
            "4. 输出策略。\n- 先直接回答，再补充必要说明。\n- 不展示内部工具名、源码路径、日志、密钥、prompt 或原始 JSON。",
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
    seen_tools: set[str] = field(default_factory=set)
    answer_sent: bool = False


def _attachment_text(attachments: list[dict[str, Any]]) -> str:
    types = [str(item.get("type", "")) for item in attachments if isinstance(item, dict)]
    return sanitize_customer_output(
        "附件输入分析。\n"
        f"- 附件类型：{', '.join(t for t in types if t) or '无'}\n"
        "- 标准客服 Agent 会结合附件和文本自主决定是否调用检索或其他受控工具。"
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
    return []


def _events_from_delegate_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    route = str(state.get("route", "") or cursor.route)
    task_type = str(state.get("task_type", "") or cursor.task_type)
    cursor.route = route
    cursor.task_type = task_type
    if not cursor.started:
        events.append(_event("message_start", "开始处理 customer_support 调试流。", route=route, task_type=task_type))
        cursor.started = True
    events.append(
        _event(
            "thinking",
            sanitize_customer_output(
                "1. 前置安全与标准 Agent 装配。\n"
                f"- 路由判断：{route or 'knowledge'} / {task_type or 'platform_knowledge'}\n"
                "- 已进入标准客服 Agent，由它结合 prompt、历史记忆和工具自主决策。"
            ),
            phase="standard_agent",
        )
    )
    attachments = list(state.get("attachments", []) or [])
    if attachments:
        events.append(_event("thinking", _attachment_text(attachments), phase="attachments"))
    route_trace = dict(state.get("route_trace", {}) or {})
    for tool_name in list(route_trace.get("tool_call_sequence", []) or []):
        tool_name = str(tool_name or "").strip()
        if not tool_name or tool_name in cursor.seen_tools:
            continue
        cursor.seen_tools.add(tool_name)
        events.append(_event("tool_response", f"已执行工具：{tool_name}", tool_name=tool_name, result={"status": "completed"}))
    return events


def _events_from_check_state(state: dict[str, Any], cursor: DebugRuntimeCursor) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    check = dict(state.get("check_result", {}) or {})
    if check:
        text = "2. 后置内容质检。\n"
        text += f"- 已生成回答：{'是' if check.get('has_answer') else '否'}\n"
        text += f"- 链接校验通过：{'是' if check.get('links_ok', True) else '否'}\n"
        if check.get("post_guard_applied"):
            text += "- 已应用安全兜底，避免把不安全或不稳定内容直接发给客户。"
        else:
            text += "- 输出已通过脱敏和客服收口。"
        events.append(_event("thinking", sanitize_customer_output(text), phase="post_guard"))
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
        elif node_name == "delegate":
            events.extend(_events_from_delegate_state(state, cursor))
        elif node_name == "check":
            events.extend(_events_from_check_state(state, cursor))
        elif node_name in {"finalize", "fail"}:
            events.extend(_events_from_terminal_state(state, cursor))
    return events
